"""Interaction with OpenAI API"""
import copy
import logging

import openai
from openai.openai_object import OpenAIObject

_cfg: dict = {}


async def init_openai(api_key: str, config: dict) -> None:
    """Set openAI configuration."""
    _cfg.update(config)
    openai.api_key = api_key


async def get_reply(user: str, messages: list, max_tokens: int) -> OpenAIObject:
    kwargs = copy.deepcopy(_cfg)
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    kwargs.setdefault("messages", []).extend(messages)
    kwargs["user"] = user
    logging.debug("user message: %s", kwargs)
    return await openai.ChatCompletion.acreate(**kwargs)
