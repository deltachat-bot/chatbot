"""Event Hooks"""
# pylama:ignore=W0603
import asyncio
import json
import logging
import os
from argparse import Namespace

import openai
import tiktoken
from deltabot_cli import AttrDict, Bot, BotCli, EventType, const, events

from .openai import get_reply, init_openai
from .orm import init as init_db
from .quota import QuotaManager
from .utils import human_time_duration, run_in_background

cli = BotCli("chatbot")
cfg: dict = {}
quota_manager = QuotaManager(cli, {})


@cli.on_init
async def on_init(bot: Bot, _args: Namespace) -> None:
    if not await bot.account.get_config("displayname"):
        await bot.account.set_config("displayname", "ChatBot")
        status = "I am a conversational Delta Chat bot, you can chat with me in private"
        await bot.account.set_config("selfstatus", status)


@cli.on_start
async def _on_start(bot: Bot, args: Namespace) -> None:
    global quota_manager  # pylint:disable=C0103
    path = os.path.join(args.config_dir, "config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as config:
            cfg.update(json.load(config))
    cfg["openai"] = {"model": "gpt-3.5-turbo", "n": 1}.update(cfg.get("openai") or {})
    api_key = cfg.get("api_key", "")
    assert api_key, "API key is not set"
    await init_openai(api_key, cfg["openai"])

    path = os.path.join(args.config_dir, "sqlite.db")
    await init_db(f"sqlite+aiosqlite:///{path}")

    quota_manager = QuotaManager(cli, cfg)
    run_in_background(quota_manager.cooldown_loop())
    logging.info(
        "Listening for messages at: %s", await bot.account.get_config("configured_addr")
    )


@cli.on(events.RawEvent)
async def log_event(event: AttrDict) -> None:
    if event.type == EventType.INFO:
        logging.info(event.msg)
    elif event.type == EventType.WARNING:
        logging.warning(event.msg)
    elif event.type == EventType.ERROR:
        logging.error(event.msg)


@cli.on(events.NewMessage(is_info=False, func=cli.is_not_known_command))
async def _filter_messages(event: AttrDict) -> None:
    msg = event.message_snapshot
    chat = await msg.chat.get_basic_snapshot()
    if chat.chat_type != const.ChatType.SINGLE or not msg.text:
        return

    max_prompt_tokens = int(cfg.get("max_prompt_tokens") or 0)
    enc = tiktoken.encoding_for_model(cfg["openai"].get("model"))
    if max_prompt_tokens and len(enc.encode(msg.text)) > max_prompt_tokens:
        await msg.chat.send_message(text="TL;DR", quoted_msg=msg.id)
    else:
        global_quota_exceeded = await quota_manager.global_quota_exceeded()
        if global_quota_exceeded:
            cooldown = human_time_duration(await quota_manager.get_global_cooldown())
            await msg.chat.send_message(
                text=f"Quota exceeded, wait for: ⏰ {cooldown}", quoted_msg=msg.id
            )
            return

        quota_exceeded = await quota_manager.quota_exceeded(msg.from_id)
        if quota_exceeded:
            cooldown = human_time_duration(quota_exceeded)
            await msg.chat.send_message(
                text=f"Quota exceeded, wait for: ⏰ {cooldown}", quoted_msg=msg.id
            )
            return

        if quota_manager.is_rate_limited():
            await msg.chat.send_message(
                text="I'm not available right now, try again later", quoted_msg=msg.id
            )
            return

        try:
            reply = await get_reply(str(msg.from_id), msg.text)
            await quota_manager.increase_usage(msg.from_id, reply.usage.total_tokens)
            text = reply.choices[0].message.content.strip()
            await msg.chat.send_message(text=text, quoted_msg=msg.id)
            await asyncio.sleep(1)  # avoid rate limits
        except openai.error.RateLimitError:
            quota_manager.set_rate_limit(60)
