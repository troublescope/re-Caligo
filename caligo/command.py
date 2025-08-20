import asyncio
from datetime import datetime
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Coroutine,
    Iterable,
    Optional,
    Sequence,
    Type,
    TypeVar,
    Union,
)

from pyrogram.enums import ParseMode
from pyrogram.filters import Filter
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import Chat, LinkPreviewOptions, Message, Update

from caligo import util

if TYPE_CHECKING:
    from .core import Caligo

CommandFunc = Union[
    Callable[..., Coroutine[Any, Any, None]], Callable[..., Coroutine[Any, Any, Any]]
]
Decorator = Callable[[CommandFunc], CommandFunc]

T = TypeVar("T", bound=Update)


def desc(_desc: str) -> Decorator:
    """Sets description on a command function."""

    def desc_decorator(func: CommandFunc) -> CommandFunc:
        setattr(func, "_cmd_description", _desc)
        return func

    return desc_decorator


def usage(_usage: str, optional: bool = False, reply: bool = False) -> Decorator:
    """Sets argument usage help on a command function."""

    def usage_decorator(func: CommandFunc) -> CommandFunc:
        setattr(func, "_cmd_usage", _usage)
        setattr(func, "_cmd_usage_optional", optional)
        setattr(func, "_cmd_usage_reply", reply)
        return func

    return usage_decorator


def alias(*aliases: str) -> Decorator:
    """Sets aliases on a command function."""

    def alias_decorator(func: CommandFunc) -> CommandFunc:
        setattr(func, "_cmd_aliases", aliases)
        return func

    return alias_decorator


def filters(_filters: Optional[Filter] = None) -> Decorator:
    """Sets filters on a command function."""

    def filter_decorator(func: CommandFunc) -> CommandFunc:
        setattr(func, "_cmd_filters", _filters)
        return func

    return filter_decorator


class Command:
    name: str
    desc: Optional[str]
    usage: Optional[str]
    usage_optional: bool
    usage_reply: bool
    aliases: Iterable[str]
    filters: Optional[Filter]
    module: Any
    func: CommandFunc

    def __init__(
        self,
        name: str,
        mod: Any,
        func: CommandFunc,
        filters: Optional[Filter] = None,
        desc: Optional[str] = None,
        usage: Optional[str] = None,
        usage_optional: bool = False,
        usage_reply: bool = False,
        aliases: Iterable[str] = [],
    ) -> None:
        self.name = name
        self.module = mod
        self.func = func
        self.filters = filters
        self.desc = desc
        self.usage = usage
        self.usage_optional = usage_optional
        self.usage_reply = usage_reply
        self.aliases = aliases

    def __repr__(self) -> str:
        return f"<command module '{self.name}' from '{self.module.name}'>"


class Context:
    bot: "Caligo"
    chat: Chat
    msg: Message
    message: Message
    reply_msg: Optional[Message]
    segments: Sequence[str]
    cmd_len: int
    invoker: str
    flags: dict[str, Any]

    last_update_time: Optional[datetime]

    response: Message
    response_mode: Optional[str]

    input: str

    def __init__(
        self,
        bot: "Caligo",
        message: Message,
        cmd_len: int,
        flags: Optional[dict[str, Any]] = None,
    ) -> None:
        self.bot = bot
        self.chat = message.chat
        self.msg = message
        self.message = message
        self.reply_msg = message.reply_to_message
        self.segments = message.command
        self.cmd_len = cmd_len
        self.invoker = self.segments[0]

        self.last_update_time = None

        self.response = None  # type: ignore
        self.response_mode = None

        self.input = (
            self.msg.content.markdown[self.cmd_len :] if self.msg.content else ""
        )

        self.flags = flags if flags is not None else self._parse_flags()

    def _parse_flags(self) -> dict[str, Any]:
        """Parse flags from ctx.input (already Markdown-parsed)."""
        flags: dict[str, Any] = {}
        tokens = self.input.split()

        has_flag = any(t.startswith("-") for t in tokens)

        if has_flag:
            current_flag: Optional[str] = None
            buffer: list[str] = []

            def commit():
                if current_flag is not None:
                    flags[current_flag] = " ".join(buffer) if buffer else True

            for token in tokens:
                if token.startswith("--") and "=" in token:
                    commit()
                    key, val = token[2:].split("=", 1)
                    flags[util.text.strip_md_key(key)] = val
                    current_flag = None
                    buffer = []
                elif (
                    token.startswith("-")
                    and not token[1:].replace(".", "", 1).isdigit()
                ):
                    commit()
                    current_flag = util.text.strip_md_key(token.lstrip("-"))
                    buffer = []
                else:
                    buffer.append(token)

            commit()

        else:
            # fallback mode: key value key value ...
            i = 0
            while i < len(tokens):
                key = util.text.strip_md_key(tokens[i])
                value: Any = True
                if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                    value = tokens[i + 1]
                    i += 1
                flags[key] = value
                i += 1

        return flags

    async def listen(
        self, handler_type: Type, filters: Filter, timeout: int = 10, group: int = -999
    ) -> T | None:
        """
        Listen for a specific type of update with given filters.

        Args:
            handler_type: The handler type (MessageHandler, CallbackQueryHandler, etc.)
            filters: Pyrogram filters to match against
            timeout: Maximum time to wait for the update (in seconds)
            group: Handler group number

        Returns:
            The matching update or None if timeout occurred
        """
        future = asyncio.get_running_loop().create_future()

        async def _callback(_, update: T):
            if not future.done():
                future.set_result(update)

        handler = handler_type(_callback, filters)
        client = getattr(self.bot, "client", self.bot)
        client.add_handler(handler, group)

        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            client.remove_handler(handler, group)

    async def listen_message(
        self, filters: Optional[Filter] = None, timeout: int = 10, group: int = -999
    ) -> Optional[Message]:
        """
        Convenience method to listen for message updates.

        Args:
            filters: Pyrogram filters to match against (defaults to text & incoming)
            timeout: Maximum time to wait for the message (in seconds)
            group: Handler group number

        Returns:
            The matching message or None if timeout occurred
        """
        from pyrogram import filters as f

        filters = filters or (f.text & f.incoming)
        return await self.listen(MessageHandler, filters, timeout, group)

    async def listen_callback(
        self, filters: Optional[Filter] = None, timeout: int = 10, group: int = -999
    ) -> Optional[Any]:  # CallbackQuery type
        """
        Convenience method to listen for callback query updates.

        Args:
            filters: Pyrogram filters to match against
            timeout: Maximum time to wait for the callback (in seconds)
            group: Handler group number

        Returns:
            The matching callback query or None if timeout occurred
        """
        return await self.listen(
            CallbackQueryHandler, filters or Filter(), timeout, group
        )

    async def _delete(
        self, delay: Optional[float] = None, message: Optional[Message] = None
    ) -> None:
        content = message or self.response
        if not content:
            return

        if delay:

            async def delete_later():
                await asyncio.sleep(delay)
                await content.delete(True)

            self.bot.loop.create_task(delete_later())
        else:
            await content.delete(True)

    async def respond(
        self,
        text: str = "",
        *,
        mode: Optional[str] = "edit",
        redact: bool = False,
        msg: Optional[Message] = None,
        reuse_response: bool = False,
        delete_after: Optional[Union[int, float]] = None,
        multi: bool = False,
        parse_mode: Optional[ParseMode] = None,
        preview: bool = False,  # 👈 added explicit toggle
        **kwargs: Any,
    ) -> Message:
        """
        Send a response message with flexible behavior.

        Args:
            text (str): The message content to send.
            mode (str, optional): 'edit', 'reply', or 'repost'.
            redact (bool): Whether to redact sensitive content.
            msg (Message, optional): The message to respond to. Defaults to ctx.msg.
            reuse_response (bool): Reuse the previous response if possible.
            delete_after (float | int, optional): Deletes the response after N seconds.
            multi (bool): Split long messages into multiple parts.
            parse_mode (ParseMode, optional): e.g., ParseMode.HTML.
            preview (bool): Whether to show link previews (default: False).
            **kwargs: Passed to Pyrogram send/edit methods.

        Returns:
            Message: The sent or edited response message.
        """
        msg = msg or self.msg

        if redact:
            text = self.bot.redact_message(text)

        # Multi-page handling
        if multi and len(text) > util.tg.MESSAGE_CHAR_LIMIT:
            return await self.respond_split(
                text, mode=mode, parse_mode=parse_mode, preview=preview, **kwargs
            )

        # Apply parse_mode
        if parse_mode is not None:
            kwargs["parse_mode"] = parse_mode

        # Always apply link preview option
        kwargs["link_preview_options"] = LinkPreviewOptions(is_disabled=not preview)

        # Handle modes
        if mode == "edit":
            self.response = await msg.edit(text=text, **kwargs)

        elif mode == "reply":
            if reuse_response and self.response:
                self.response = await self.response.edit(text=text, **kwargs)
            else:
                self.response = await msg.reply(text, **kwargs)

        elif mode == "repost":
            self.response = await msg.reply(text, **kwargs)
            await msg.delete()

        else:
            raise ValueError(f"Unknown response mode '{mode}'")

        self.response_mode = mode

        if delete_after:
            await self._delete(delete_after)
            self.response = None  # type: ignore

        return self.response  # type: ignore
