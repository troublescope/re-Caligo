import io
import re
import uuid
from enum import IntEnum, unique
from typing import Any, List, Optional, Tuple, Union

import bprint
import pyrogram
from pyrogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

MESSAGE_CHAR_LIMIT = 4096
TRUNCATION_SUFFIX = "... (truncated)"
Button = Union[Tuple[Tuple[str, str, bool]], List[Tuple[str, str, bool]]]


SKIP_ATTR_NAMES = (
    "CONSTRUCTOR_ID",
    "SUBCLASS_OF_ID",
    "access_hash",
    "message",
    "raw_text",
    "phone",
)
SKIP_ATTR_VALUES = (False,)
SKIP_ATTR_TYPES = ()


@unique
class Types(IntEnum):
    """A Class representing message type"""

    TEXT = 0
    BUTTON_TEXT = 1
    DOCUMENT = 2
    PHOTO = 3
    VIDEO = 4
    STICKER = 5
    AUDIO = 6
    VOICE = 7
    VIDEO_NOTE = 8
    ANIMATION = 9


def mention_user(user: pyrogram.types.User) -> str:
    """Returns a string that mentions the given user, regardless of whether they have a username."""

    if user.username:
        # Use username mention if possible
        name = f"@{user.username}"
    else:
        # Use the first and last name otherwise
        if user.first_name and user.last_name:
            name = user.first_name + " " + user.last_name
        elif user.first_name and not user.last_name:
            name = user.first_name
        else:
            # Deleted accounts have no name; behave like the official clients
            name = "Deleted Account"

    return f"[{name}](tg://user?id={user.id})"


async def extract_user_id(
    message: pyrogram.types.Message, user_input: Union[str, int]
) -> Optional[int]:
    """
    Extracts user ID from username, user ID, or User object using resolve_peer.
    Only returns user IDs, not channel or chat IDs.

    Args:
        message: Pyrogram Message Object
        user_input: Username (with or without @ prefix), user ID as int/str, or User object

    Returns:
        User ID as integer if user_input belongs to a user, None otherwise
    """
    try:
        # If it's already an integer, return it directly
        if isinstance(user_input, int):
            return user_input

        # If it's a User object, return its ID
        if hasattr(user_input, "id") and isinstance(user_input.id, int):
            return user_input.id

        # Convert to string and try to parse as integer first
        user_str = str(user_input).strip()

        # Try parsing as integer (user ID)
        try:
            return int(user_str)
        except ValueError:
            pass

        # If not a number, treat as username and resolve
        username = user_str.lstrip("@")
        if not username:
            return None

        peer = await message._client.resolve_peer(username)

        if isinstance(peer, pyrogram.raw.types.InputPeerUser):
            return peer.user_id
        else:
            return None  # Not a user (could be channel/group)

    except (
        pyrogram.errors.UsernameNotOccupied,
        pyrogram.errors.UsernameInvalid,
        pyrogram.errors.PeerIdInvalid,
    ):
        return None
    except Exception:
        return None


def filter_code_block(inp: str) -> str:
    """Returns the content inside the given Markdown code block or inline code."""

    if inp.startswith("```") and inp.endswith("```"):
        inp = inp[3:][:-3]
    elif inp.startswith("`") and inp.endswith("`"):
        inp = inp[1:][:-1]

    return inp


def _bprint_skip_predicate(name: str, value: Any) -> bool:
    return (
        name.startswith("_")
        or value is None
        or callable(value)
        or name in SKIP_ATTR_NAMES
        or value in SKIP_ATTR_VALUES
        or type(value) in SKIP_ATTR_TYPES
    )


def pretty_print_entity(entity: Any) -> str:
    """Pretty-prints the given Telegram entity with recursive details."""

    return bprint.bprint(entity, stream=str, skip_predicate=_bprint_skip_predicate)


def truncate(text: str) -> str:
    """Truncates the given text to fit in one Telegram message."""
    suffix = TRUNCATION_SUFFIX
    if text.endswith("```"):
        suffix += "```"

    if len(text) > MESSAGE_CHAR_LIMIT:
        return text[: MESSAGE_CHAR_LIMIT - len(suffix)] + suffix

    return text


async def send_as_document(
    content: str, msg: pyrogram.types.Message, caption: str
) -> pyrogram.types.Message:
    with io.BytesIO(str.encode(content)) as o:
        o.name = str(uuid.uuid4()).split("-")[0].upper() + ".TXT"
        return await msg.reply_document(
            document=o,
            caption="❯ ```" + caption + "```",
        )


def get_message_info(msg: Message) -> Tuple[str, Types, Optional[str], Button]:
    """Parse received message and return its content."""
    types = None
    content = None
    text = ""
    buttons = []

    reply_msg = msg.reply_to_message

    if reply_msg:
        text = reply_msg.text or reply_msg.caption
        added_text = None
        if text:
            text, buttons = parse_button(text.markdown)
        else:
            # added_text are from user input
            added_text, buttons = parse_button(msg.text.markdown.split(" ", 2)[-1])

        if not text and added_text is not None:
            text = added_text

        if reply_msg.text:
            types = Types.BUTTON_TEXT if buttons else Types.TEXT
        elif reply_msg.sticker:
            content, types = reply_msg.sticker.file_id, Types.STICKER
        elif reply_msg.document:
            content, types = reply_msg.document.file_id, Types.DOCUMENT
        elif reply_msg.photo:
            content, types = reply_msg.photo.file_id, Types.PHOTO
        elif reply_msg.audio:
            content, types = reply_msg.audio.file_id, Types.AUDIO
        elif reply_msg.voice:
            content, types = reply_msg.voice.file_id, Types.VOICE
        elif reply_msg.video:
            content, types = reply_msg.video.file_id, Types.VIDEO
        elif reply_msg.video_note:
            content, types = reply_msg.video_note.file_id, Types.VIDEO_NOTE
        elif reply_msg.animation:
            content, types = reply_msg.animation.file_id, Types.ANIMATION
        else:
            raise ValueError("Can't get message information")
    else:
        raw_text = msg.text.markdown.split(" ", 2)
        if len(raw_text) == 2:  # content were on the next line
            raw_text = raw_text[1]
            text, buttons = parse_button(raw_text.split("\n", 1)[1])
        else:
            text, buttons = parse_button(raw_text[2])
        types = Types.BUTTON_TEXT if buttons else Types.TEXT

    return text, types, content, buttons


def parse_button(text: str) -> Tuple[str, Button]:
    """Parse button to save"""
    regex = re.compile(r"(\[([^\[]+?)\]\(button(url|copy):(?:/{0,2})(.+?)(:same)?\))")

    prev = 0
    parser_data = ""
    buttons = []  # type: List[Tuple[str, str, bool]]
    for match in regex.finditer(text):
        # escape check
        md_escaped = 0
        to_check = match.start(1) - 1
        while to_check > 0 and text[to_check] == "\\":
            md_escaped += 1
            to_check -= 1

        # if != "escaped" -> Create button: btn
        if md_escaped % 2 == 0:
            label = match.group(2)
            _type = match.group(3)
            _text = match.group(4)
            _same = bool(match.group(5))

            # create a thruple with button label, url, and newline status
            buttons.append((label, _type, _text, _same))
            parser_data += text[prev : match.start(1)]
            prev = match.end(1)
        # if odd, escaped -> move along
        else:
            parser_data += text[prev:to_check]
            prev = match.start(1) - 1

    parser_data += text[prev:]
    # Remove any markdown button left over if any
    # t = parser_data.rstrip().split()
    # if t:
    #     pattern = re.compile(r"[_-`*~]+")
    #     anyMarkdownLeft = pattern.search(t[-1])
    #     if anyMarkdownLeft:
    #         toRemove = anyMarkdownLeft[0][0]
    #         t[-1] = t[-1].replace(toRemove, "")
    #         return " ".join(t), buttons

    return parser_data.rstrip(), buttons


def build_button(buttons: Button) -> InlineKeyboardMarkup:
    """Build saved button format"""
    keyb = []  # type: List[List[InlineKeyboardButton]]
    for data in buttons:
        ikb = None
        label, _type, _text, _same = data
        if _type == "url":
            ikb = InlineKeyboardButton(label, url=_text)
        else:
            ikb = InlineKeyboardButton(label, copy_text=CopyTextButton(text=_text))
        if ikb:
            if _same and keyb:
                keyb[-1].append(ikb)
            else:
                keyb.append([ikb])

    return InlineKeyboardMarkup(keyb)


def revert_button(button: Button) -> str:
    """Revert button format"""
    res = ""
    for btn in button:
        if btn[2]:
            res += f"\n[{btn[0]}](buttonurl://{btn[1]}:same)"
        else:
            res += f"\n[{btn[0]}](buttonurl://{btn[1]})"
    return res
