"""Interaction with OpenAI API"""
import openai
from openai.openai_object import OpenAIObject

_cfg: dict = {}


async def init_openai(api_key: str, config: dict) -> None:
    """Set openAI configuration."""
    _cfg.update(config)
    openai.api_key = api_key


async def get_reply(user: str, text: str) -> OpenAIObject:
    return await openai.ChatCompletion.acreate(
        **_cfg,
        user=user,
        messages=[{"role": "user", "content": text}],
    )
