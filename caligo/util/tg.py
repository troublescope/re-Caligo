import io
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union

import bprint
import pyrogram
from pyrogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InlineQueryResultCachedAnimation,
    InlineQueryResultCachedAudio,
    InlineQueryResultCachedDocument,
    InlineQueryResultCachedPhoto,
    InlineQueryResultCachedSticker,
    InlineQueryResultCachedVideo,
    InlineQueryResultCachedVoice,
    InputMediaAnimation,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    InputTextMessageContent,
    Message,
)

MESSAGE_CHAR_LIMIT = 4096
TRUNCATION_SUFFIX = "... (truncated)"
Button = List[Tuple[str, str, str, bool]]

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

INPUT_MEDIA = {
    "animation": InputMediaAnimation,
    "audio": InputMediaAudio,
    "document": InputMediaDocument,
    "photo": InputMediaPhoto,
    "video": InputMediaVideo,
}

INLINE_RESULT = {
    "text": InlineQueryResultArticle,
    "animation": InlineQueryResultCachedAnimation,
    "audio": InlineQueryResultCachedAudio,
    "document": InlineQueryResultCachedDocument,
    "photo": InlineQueryResultCachedPhoto,
    "sticker": InlineQueryResultCachedSticker,
    "video": InlineQueryResultCachedVideo,
    "voice": InlineQueryResultCachedVoice,
}


def generate_input_media(
    _type: str,
    text: str = None,
    file_id: str = None,
    buttons: InlineKeyboardMarkup = None,
) -> Dict[str, Any]:
    return {"media": INPUT_MEDIA[_type](file_id, caption=text), "reply_markup": buttons}


def generate_inline_result(
    _type: str,
    text: str = None,
    file_id: str = None,
    buttons: InlineKeyboardMarkup = None,
) -> Dict[str, Any]:
    if _type == "text":
        return {
            "results": [
                InlineQueryResultArticle(
                    title="Send as text",
                    input_message_content=InputTextMessageContent(message_text=text),
                    reply_markup=buttons,
                )
            ],
            "cache_time": 900,
        }

    return {
        "results": [
            INLINE_RESULT[_type](
                **{
                    "reply_markup": buttons,
                    f"{_type}_file_id": file_id,
                    **(
                        {"title": "Send as Media"}
                        if _type not in ["sticker", "audio"]
                        else {}
                    ),
                    **({"caption": text} if _type != "sticker" else {}),
                }
            )
        ],
        "cache_time": 900,
    }


def mention_user(user: pyrogram.types.User) -> str:
    if user.username:
        name = f"@{user.username}"
    else:
        name = user.first_name or ""
        if user.last_name:
            name += f" {user.last_name}"
        if not name.strip():
            name = "Deleted Account"
    return f"[{name}](tg://user?id={user.id})"


async def extract_user_id(
    message: pyrogram.types.Message, user_input: Union[str, int, pyrogram.types.User]
) -> Optional[int]:
    if isinstance(user_input, int):
        return user_input
    if isinstance(user_input, pyrogram.types.User):
        return user_input.id

    user_str = str(user_input).strip()
    if user_str.isnumeric():
        return int(user_str)

    try:
        username = user_str.lstrip("@")
        if not username:
            return None
        peer = await message._client.resolve_peer(username)
        if isinstance(peer, pyrogram.raw.types.InputPeerUser):
            return peer.user_id
    except (
        pyrogram.errors.UsernameNotOccupied,
        pyrogram.errors.UsernameInvalid,
        pyrogram.errors.PeerIdInvalid,
    ):
        return None
    return None


def filter_code_block(inp: str) -> str:
    if inp.startswith("```") and inp.endswith("```"):
        return inp[3:-3]
    if inp.startswith("`") and inp.endswith("`"):
        return inp[1:-1]
    return inp


def _bprint_skip_predicate(name: str, value: Any) -> bool:
    return (
        name.startswith("_")
        or value is None
        or callable(value)
        or name in SKIP_ATTR_NAMES
        or value in SKIP_ATTR_VALUES
        or isinstance(value, SKIP_ATTR_TYPES)
    )


def pretty_print_entity(entity: Any) -> str:
    return bprint.bprint(entity, stream=str, skip_predicate=_bprint_skip_predicate)


def truncate(text: str) -> str:
    if len(text) <= MESSAGE_CHAR_LIMIT:
        return text
    suffix = TRUNCATION_SUFFIX
    if text.endswith("```"):
        suffix += "```"
    return text[: MESSAGE_CHAR_LIMIT - len(suffix)] + suffix


async def send_as_document(
    content: str, msg: pyrogram.types.Message, caption: str
) -> pyrogram.types.Message:
    with io.BytesIO(content.encode()) as doc_file:
        doc_file.name = f"{uuid.uuid4().hex[:8].upper()}.TXT"
        return await msg.reply_document(document=doc_file, caption=f"❯ ```{caption}```")


def extract_message(
    msg: Message,
) -> Tuple[str, Optional[str], Optional[str], Optional[Button]]:
    target_msg = msg.reply_to_message or msg
    text_content = target_msg.text or target_msg.caption or ""
    _text, _btns = parse_button(text_content)
    _type = "text"
    _file = None

    if target_msg.media:
        _type = target_msg.media.value
        media = getattr(target_msg, _type)
        if target_msg.photo:
            _file = media.sizes[-1].file_id
        elif hasattr(media, "file_id"):
            _file = media.file_id

    if target_msg.reply_markup and isinstance(
        target_msg.reply_markup, InlineKeyboardMarkup
    ):
        _btns.extend(extract_inline_keyboard(target_msg.reply_markup.inline_keyboard))

    return _type, _text, _file, _btns


def extract_inline_keyboard(
    inline_keyboard: List[List[InlineKeyboardButton]],
) -> Button:
    results = []
    for row in inline_keyboard:
        for i, btn in enumerate(row):
            text = btn.text
            same = i > 0
            if btn.callback_data:
                results.append((text, "data", btn.callback_data, same))
            elif btn.url:
                results.append((text, "url", btn.url, same))
            elif btn.copy_text:
                results.append((text, "copy", btn.copy_text, same))
    return results


def parse_button(text: str) -> Tuple[str, Button]:
    """Parse button to save"""
    prev = 0
    parser_data = ""
    buttons = []  # type: List[Tuple[str, str, bool]]

    regex = re.compile(
        r"(\[([^\[\]]+)\]\(button(url|data|copy):(?://)?(.+?)(:same)?\))"
    )
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


def build_button(buttons: Button) -> Optional[InlineKeyboardMarkup]:
    if not buttons:
        return None
    rows = []
    for label, _type, content, same in buttons:
        btn = None
        if _type == "url":
            btn = InlineKeyboardButton(label, url=content)
        elif _type == "data":
            btn = InlineKeyboardButton(label, callback_data=content)
        elif _type == "copy":
            btn = InlineKeyboardButton(label, copy_text=CopyTextButton(text=content))

        if btn:
            if same and rows:
                rows[-1].append(btn)
            else:
                rows.append([btn])
    return InlineKeyboardMarkup(rows) if rows else None


def revert_button(button: Button) -> str:
    res = ""
    for label, _type, text, same in button:
        same_marker = ":same" if same else ""
        res += f"\n[{label}](button{_type}://{text}{same_marker})"
    return res
