"""Event Hooks"""

import time
from argparse import Namespace
from typing import List

from deltabot_cli import BotCli
from deltachat2 import (
    Bot,
    ChatType,
    CoreEvent,
    EventType,
    Message,
    MsgData,
    NewMsgEvent,
    SpecialContactId,
    events,
)
from rich.logging import RichHandler

from .gpt4all import GPT4All

cli = BotCli("chatbot")
cli.add_generic_option(
    "--no-time",
    help="do not display date timestamp in log messages",
    action="store_false",
)
cli.add_generic_option(
    "--model",
    help="gpt4all model to use (default: %(default)s)",
    default="mistral-7b-openorca.gguf2.Q4_0.gguf",
)
cli.add_generic_option("--system-prompt", help="an initial instruction for the model")
cli.add_generic_option(
    "--max-tokens",
    help="the maximum number of tokens to generate (default: %(default)s)",
    default=500,
    type=int,
)
cli.add_generic_option(
    "--history",
    help="the maximum number replies to rember (default: %(default)s)",
    default=20,
    type=int,
)
cli.add_generic_option(
    "--temperature",
    help="the model temperature. Larger values increase creativity but decrease factuality."
    " (default: %(default)s)",
    default=0.5,
    type=float,
)

gpt4all: GPT4All = None
args = Namespace()


@cli.on_init
def on_init(bot: Bot, opts: Namespace) -> None:
    bot.logger.handlers = [
        RichHandler(show_path=False, omit_repeated_times=False, show_time=opts.no_time)
    ]
    for accid in bot.rpc.get_all_account_ids():
        if not bot.rpc.get_config(accid, "displayname"):
            bot.rpc.set_config(accid, "displayname", "ChatBot")
            status = "I am a conversational bot, you can chat with me in private"
            bot.rpc.set_config(accid, "selfstatus", status)


@cli.on_start
def on_start(_bot: Bot, opts: Namespace) -> None:
    global gpt4all, args  # noqa
    args = opts
    gpt4all = GPT4All(args.model)


@cli.on(events.RawEvent)
def log_event(bot: Bot, accid: int, event: CoreEvent) -> None:
    if event.kind == EventType.INFO:
        bot.logger.debug(event.msg)
    elif event.kind == EventType.WARNING:
        bot.logger.warning(event.msg)
    elif event.kind == EventType.ERROR:
        bot.logger.error(event.msg)
    elif event.kind == EventType.SECUREJOIN_INVITER_PROGRESS:
        if event.progress == 1000:
            if not bot.rpc.get_contact(accid, event.contact_id).is_bot:
                bot.logger.debug("QR scanned by contact id=%s", event.contact_id)
                chatid = bot.rpc.create_chat_by_contact_id(accid, event.contact_id)
                send_help(bot, accid, chatid)


@cli.on(events.NewMessage(command="/help"))
def _help(bot: Bot, accid: int, event: NewMsgEvent) -> None:
    bot.rpc.markseen_msgs(accid, [event.msg.id])
    send_help(bot, accid, event.msg.chat_id)


@cli.on(events.NewMessage(command="/clear"))
def _clear(bot: Bot, accid: int, event: NewMsgEvent) -> None:
    bot.rpc.markseen_msgs(accid, [event.msg.id])
    bot.rpc.delete_chat(accid, event.msg.chat_id)


@cli.on(events.NewMessage(is_info=False))
def on_message(bot: Bot, accid: int, event: NewMsgEvent) -> None:
    if bot.has_command(event.command):
        return

    msg = event.msg
    chat = bot.rpc.get_basic_chat_info(accid, msg.chat_id)
    if chat.chat_type != ChatType.SINGLE:
        return

    bot.rpc.markseen_msgs(accid, [msg.id])

    if not msg.text:
        send_help(bot, accid, msg.chat_id)
        return

    bot.rpc.send_reaction(accid, msg.id, ["⏳"])
    with gpt4all.chat_session(system_prompt=args.system_prompt or None):
        bot.logger.debug(f"[chat={msg.chat_id}] Processing message={msg.id}")
        load_history(bot, accid, msg.chat_id)

        start = time.time()
        text = gpt4all.generate(
            msg.text, max_tokens=args.max_tokens, temp=args.temperature
        )
        text = text.strip() or "😶"
        took = time.time() - start
        bot.logger.debug(f"[chat={msg.chat_id}] Generated reply in {took:.1f} seconds")

    bot.rpc.send_reaction(accid, msg.id, [])
    bot.rpc.send_msg(accid, msg.chat_id, MsgData(text=text, quoted_message_id=msg.id))


@cli.after(events.NewMessage)
def delete_msgs(bot: Bot, accid: int, event: NewMsgEvent) -> None:
    if event.command != "/clear":  # /clear deletes the whole chat
        msg = event.msg
        bot.rpc.delete_messages(accid, [msg.id])
        bot.logger.debug(f"[chat={msg.chat_id}] Deleted message={msg.id}")


def load_history(bot: Bot, accid: int, chatid: int) -> None:
    to_process: List[Message] = []
    to_delete: List[int] = []
    for msgid in reversed(bot.rpc.get_message_ids(accid, chatid, False, False)):
        oldmsg = bot.rpc.get_message(accid, msgid)
        if oldmsg.from_id == SpecialContactId.SELF:
            if len(to_process) >= args.history:
                to_delete.append(msgid)
            else:
                to_process.append(oldmsg)

    if to_delete:
        bot.rpc.delete_messages(accid, to_delete)

    start = time.time()
    for oldmsg in reversed(to_process):
        prompt = oldmsg.quote.text if oldmsg.quote else ""
        gpt4all.generate(prompt, max_tokens=0, fake_reply=oldmsg.text)
    took = time.time() - start
    bot.logger.debug(
        f"[chat={chatid}] Loaded {len(to_process)} entries of history in {took:.1f} seconds"
    )


def send_help(bot: Bot, accid: int, chatid: int) -> None:
    lines = [
        "👋 I am a conversational bot and you can chat with me in private only.",
        "No 3rd party service is involved, only I will have access to the messages you send to me.",
        'To control our chat history, you should set "Disappearing Messages" in this chat.',
        "Alternatively, send /clear and I will forget all the messages I have received here",
    ]
    bot.rpc.send_msg(accid, chatid, MsgData(text="\n".join(lines)))
