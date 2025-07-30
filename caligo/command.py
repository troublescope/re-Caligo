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

from pyrogram.filters import Filter
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import Chat, Message, Update

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
    args: Sequence[str]

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

        self.input = self.msg.text[self.cmd_len :] if self.msg.text else ""

        # Parse flags from command if not provided
        self.flags = flags if flags is not None else self._parse_flags()

    def _parse_flags(self) -> dict[str, Any]:
        """Parse flags from command segments (e.g., --flag=value or -f block until next flag)"""
        flags: dict[str, Any] = {}
        args = self.segments[1:]  # Skip command name
        current_flag: Optional[str] = None
        buffer: list[str] = []

        def commit():
            if current_flag is not None:
                flags[current_flag] = " ".join(buffer) if buffer else True

        for arg in args:
            if (
                arg.startswith("-")
                and not arg.lstrip("-").replace(".", "", 1).isdigit()
            ):
                if "=" in arg:
                    commit()
                    key, val = arg.lstrip("-").split("=", 1)
                    flags[key] = val
                    current_flag = None
                    buffer = []
                else:
                    commit()
                    current_flag = arg.lstrip("-")
                    buffer = []
            else:
                buffer.append(arg)

        commit()
        return flags

    def __getattr__(self, name: str) -> Any:
        if name == "args":
            return self._get_args()

        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{name}'"
        )

    # Argument segments
    def _get_args(self) -> Sequence[str]:
        """Get arguments, filtering out flags"""
        # Filter out flag arguments and their values
        args = []
        segments = list(self.segments[1:])  # Skip command name
        i = 0

        while i < len(segments):
            arg = segments[i]

            # Skip flag arguments
            if arg.startswith("--"):
                flag_name = arg[2:]
                if "=" not in flag_name:
                    # Check if next argument is a value for this flag
                    if i + 1 < len(segments) and not segments[i + 1].startswith("-"):
                        i += 1  # Skip the value too

            elif arg.startswith("-") and len(arg) > 1:
                # Check if next argument is a value for this flag
                if i + 1 < len(segments) and not segments[i + 1].startswith("-"):
                    i += 1  # Skip the value too

            else:
                # Regular argument
                args.append(arg)

            i += 1

        self.args = args
        return self.args

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
        # Assuming the bot has a client attribute that's the Pyrogram Client
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

        if filters is None:
            filters = f.text & f.incoming

        return await self.listen(
            handler_type=MessageHandler, filters=filters, timeout=timeout, group=group
        )

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
            handler_type=CallbackQueryHandler,
            filters=filters or Filter(),
            timeout=timeout,
            group=group,
        )

    async def _delete(
        self, delay: Optional[float] = None, message: Optional[Message] = None
    ) -> None:
        content = message or self.response
        if not content:
            return

        if delay:

            async def delete(delay: float) -> None:
                await asyncio.sleep(delay)
                await content.delete(True)

            self.bot.loop.create_task(delete(delay))
        else:
            await content.delete(True)

    async def respond(
        self,
        text: str = "",
        *,
        mode: Optional[str] = None,
        redact: bool = True,
        msg: Optional[Message] = None,
        reuse_response: bool = False,
        delete_after: Optional[Union[int, float]] = None,
        **kwargs: Any,
    ) -> Message:

        self.response = await self.bot.respond(
            msg or self.msg,
            text,
            input_arg=self.input,
            mode=mode,
            redact=redact,
            response=(
                self.response if reuse_response and mode == self.response_mode else None
            ),
            **kwargs,
        )
        self.response_mode = mode

        if delete_after:
            await self._delete(delete_after)
            self.response = None  # type: ignore

        return self.response  # type: ignore

    async def respond_split(
        self,
        text: str,
        *,
        max_pages: Optional[int] = None,  # type: ignore
        redact: Optional[bool] = None,
        **kwargs: Any,
    ) -> Message:
        if redact is None:
            redact = self.bot.config["bot"]["redact_responses"]

        if max_pages is None:
            max_pages: int = self.bot.config["bot"]["overflow_page_limit"]

        if redact:
            # Redact before splitting in case the sensitive content is on a message boundary
            text = self.bot.redact_message(text)

        pages_sent = 0
        last_msg: Message = None  # type: ignore
        while text and pages_sent < max_pages:
            # Make sure that there's an ellipsis placed at both the beginning and end,
            # depending on whether there's more content to be shown
            # The conditions are a bit complex, so just use a primitive LUT for now
            if len(text) <= 4096:
                # Low remaining content might require no ellipses
                if pages_sent == 0:
                    page = text[: util.tg.MESSAGE_CHAR_LIMIT]
                    ellipsis_chars = 0
                else:
                    page = "..." + text[: util.tg.MESSAGE_CHAR_LIMIT - 3]
                    ellipsis_chars = 3
            elif pages_sent == max_pages - 1:
                # Last page should use the standard truncation path if it's too large
                if pages_sent == 0:
                    page = text
                    ellipsis_chars = 0
                else:
                    page = "..." + text
                    ellipsis_chars = 3
            else:
                # Remaining content in other pages might need two ellipses
                if pages_sent == 0:
                    page = text[: util.tg.MESSAGE_CHAR_LIMIT - 3] + "..."
                    ellipsis_chars = 3
                else:
                    page = "..." + text[: util.tg.MESSAGE_CHAR_LIMIT - 6] + "..."
                    ellipsis_chars = 6

            last_msg = await self.respond_multi(page, **kwargs)
            text = text[util.tg.MESSAGE_CHAR_LIMIT - ellipsis_chars :]
            pages_sent += 1

        return last_msg

    async def respond_multi(
        self,
        *args: Any,
        mode: Optional[str] = None,
        msg: Message = None,  # type: ignore
        reuse_response: bool = False,
        **kwargs: Any,
    ) -> Message:
        # First response is the same
        if self.response:
            # After that, force a reply to the previous response
            if mode is None:
                mode = "reply"

            if msg is None:
                msg = self.response

            if reuse_response is None:
                reuse_response = False

        return await self.respond(
            *args, mode=mode, msg=msg, reuse_response=reuse_response, **kwargs
        )
