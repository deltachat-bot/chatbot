# pylama:ignore=C0114,W0212,R0913,R0914,C0301,R1735,
from __future__ import annotations

import ctypes
import warnings
from typing import Any

from gpt4all import GPT4All as _GPT4All
from gpt4all._pyllmodel import (
    LLModel,
    PromptCallback,
    ResponseCallback,
    ResponseCallbackType,
    empty_response_callback,
    llmodel,
)
from gpt4all.gpt4all import MessageType


def prompt_model(
    self,
    prompt: str,
    prompt_template: str,
    callback: ResponseCallbackType,
    n_predict: int = 4096,
    top_k: int = 40,
    top_p: float = 0.9,
    min_p: float = 0.0,
    temp: float = 0.1,
    n_batch: int = 8,
    repeat_penalty: float = 1.2,
    repeat_last_n: int = 10,
    context_erase: float = 0.75,
    reset_context: bool = False,
    special: bool = False,
    fake_reply: str = "",
):
    """
    Generate response from model from a prompt.

    Parameters
    ----------
    prompt: str
        Question, task, or conversation for model to respond to
    callback(token_id:int, response:str): bool
        The model sends response tokens to callback

    Returns
    -------
    None
    """

    if self.model is None:
        self._raise_closed()

    self.buffer.clear()
    self.buff_expecting_cont_bytes = 0

    self._set_context(
        n_predict=n_predict,
        top_k=top_k,
        top_p=top_p,
        min_p=min_p,
        temp=temp,
        n_batch=n_batch,
        repeat_penalty=repeat_penalty,
        repeat_last_n=repeat_last_n,
        context_erase=context_erase,
        reset_context=reset_context,
    )

    llmodel.llmodel_prompt(
        self.model,
        ctypes.c_char_p(prompt.encode()),
        ctypes.c_char_p(prompt_template.encode()),
        PromptCallback(self._prompt_callback),
        ResponseCallback(self._callback_decoder(callback)),
        True,
        self.context,
        special,
        ctypes.c_char_p(fake_reply.encode()) if fake_reply else ctypes.c_char_p(),
    )


LLModel.prompt_model = prompt_model


class GPT4All(_GPT4All):
    """Patch GPT4All to support fake_reply parameter.
    See https://github.com/nomic-ai/gpt4all/issues/1959
    """

    def generate(  # noqa
        self,
        prompt: str,
        *,
        max_tokens: int = 200,
        temp: float = 0.7,
        top_k: int = 40,
        top_p: float = 0.4,
        min_p: float = 0.0,
        repeat_penalty: float = 1.18,
        repeat_last_n: int = 64,
        n_batch: int = 8,
        n_predict: int | None = None,
        streaming: bool = False,
        fake_reply: str = "",
        callback: ResponseCallbackType = empty_response_callback,
    ) -> Any:
        """
        Generate outputs from any GPT4All model.

        Args:
            prompt: The prompt for the model to complete.
            max_tokens: The maximum number of tokens to generate.
            temp: The model temperature. Larger values increase creativity but decrease factuality.
            top_k: Randomly sample from the top_k most likely tokens at each generation step. Set this to 1 for greedy decoding.
            top_p: Randomly sample at each generation step from the top most likely tokens whose probabilities add up to top_p.
            min_p: Randomly sample at each generation step from the top most likely tokens whose probabilities are at least min_p.
            repeat_penalty: Penalize the model for repetition. Higher values result in less repetition.
            repeat_last_n: How far in the models generation history to apply the repeat penalty.
            n_batch: Number of prompt tokens processed in parallel. Larger values decrease latency but increase resource requirements.
            n_predict: Equivalent to max_tokens, exists for backwards compatibility.
            streaming: If True, this method will instead return a generator that yields tokens as the model generates them.
            fake_reply: A spoofed reply for the given prompt, used as a way to load chat history.
            callback: A function with arguments token_id:int and response:str, which receives the tokens from the model as they are generated and stops the generation by returning False.

        Returns:
            Either the entire completion or a generator that yields the completion token by token.
        """

        # Preparing the model request
        generate_kwargs: dict[str, Any] = dict(
            temp=temp,
            top_k=top_k,
            top_p=top_p,
            min_p=min_p,
            repeat_penalty=repeat_penalty,
            repeat_last_n=repeat_last_n,
            n_batch=n_batch,
            n_predict=n_predict if n_predict is not None else max_tokens,
            fake_reply=fake_reply,
        )

        if self._history is not None:
            # check if there is only one message, i.e. system prompt:
            reset = len(self._history) == 1
            self._history.append({"role": "user", "content": prompt})

            fct_func = self._format_chat_prompt_template.__func__  # type: ignore[attr-defined]
            if fct_func is GPT4All._format_chat_prompt_template:
                if reset:
                    # ingest system prompt
                    # use "%1%2" and not "%1" to avoid implicit whitespace
                    self.model.prompt_model(
                        self._history[0]["content"],
                        "%1%2",
                        empty_response_callback,
                        n_batch=n_batch,
                        n_predict=0,
                        reset_context=True,
                        special=True,
                    )
                prompt_template = self._current_prompt_template.format("%1", "%2")
            else:
                warnings.warn(
                    "_format_chat_prompt_template is deprecated. Please use a chat session with a prompt template.",
                    DeprecationWarning,
                )
                # special tokens won't be processed
                prompt = self._format_chat_prompt_template(
                    self._history[-1:],
                    self._history[0]["content"] if reset else "",
                )
                prompt_template = "%1"
                generate_kwargs["reset_context"] = reset
        else:
            prompt_template = "%1"
            generate_kwargs["reset_context"] = True

        # Prepare the callback, process the model response
        output_collector: list[MessageType]
        output_collector = [
            {"content": ""}
        ]  # placeholder for the self._history if chat session is not activated

        if self._history is not None:
            self._history.append({"role": "assistant", "content": ""})
            output_collector = self._history

        def _callback_wrapper(
            callback: ResponseCallbackType,
            output_collector: list[MessageType],
        ) -> ResponseCallbackType:
            def _callback(token_id: int, response: str) -> bool:
                nonlocal callback, output_collector

                output_collector[-1]["content"] += response

                return callback(token_id, response)

            return _callback

        # Send the request to the model
        if streaming:
            return self.model.prompt_model_streaming(
                prompt,
                prompt_template,
                _callback_wrapper(callback, output_collector),
                **generate_kwargs,
            )

        self.model.prompt_model(  # noqa
            prompt,
            prompt_template,
            _callback_wrapper(callback, output_collector),
            **generate_kwargs,
        )

        return output_collector[-1]["content"]
