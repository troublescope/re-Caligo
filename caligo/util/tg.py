import io
import uuid
from typing import Any, Optional, Union

import bprint
import pyrogram

MESSAGE_CHAR_LIMIT = 4096
TRUNCATION_SUFFIX = "... (truncated)"

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
