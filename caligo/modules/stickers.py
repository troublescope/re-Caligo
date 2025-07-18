import io
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, ClassVar

from aiopath import AsyncPath
from pyrogram.errors import StickersetInvalid
from pyrogram.raw.functions.messages import GetStickerSet, UploadMedia
from pyrogram.raw.functions.stickers import AddStickerToSet, CreateStickerSet
from pyrogram.raw.types import (
    DocumentAttributeFilename,
    InputDocument,
    InputMediaUploadedDocument,
    InputPeerSelf,
    InputStickerSetItem,
    InputStickerSetShortName,
)

from caligo import command, module, util
from caligo.core import database

MAX_VIDEO_SIZE = 5 * 1024 * 1024


class LengthMismatchError(Exception): ...


class Sticker(module.Module):
    name: ClassVar[str] = "Sticker"
    db: database.AsyncCollection

    async def on_load(self):
        self.db = self.bot.db.get_collection(self.name.upper())
        path = AsyncPath(util.tools.CACHE_PATH)
        if not await path.exists():
            await path.mkdir(parents=True)

    async def upload_media(
        self, sticker_data: str | BinaryIO | AsyncPath
    ) -> InputDocument:
        temp_path: AsyncPath | None = None
        try:
            match sticker_data:
                case str() | AsyncPath():
                    file_path = AsyncPath(sticker_data)
                    if not await file_path.exists():
                        raise FileNotFoundError(f"File not found: {file_path}")
                    filename = file_path.name
                    mime_type = (
                        mimetypes.guess_type(str(file_path))[0]
                        or "application/octet-stream"
                    )

                case io.BytesIO() | io.BufferedReader():
                    temp_path = (
                        AsyncPath(util.tools.CACHE_PATH)
                        / f"temp_sticker_{datetime.now().timestamp()}"
                    )
                    filename = getattr(sticker_data, "name", "sticker")
                    content = sticker_data.read()
                    sticker_data.seek(0)
                    await temp_path.write_bytes(content)
                    file_path = temp_path
                    mime_type = (
                        mimetypes.guess_type(filename)[0] or "application/octet-stream"
                    )

                case _:
                    raise TypeError("Unsupported sticker_data type")

            file = await self.bot.client.save_file(str(file_path))
            upload = await self.bot.client.invoke(
                UploadMedia(
                    peer=InputPeerSelf(),
                    media=InputMediaUploadedDocument(
                        file=file,
                        mime_type=mime_type,
                        attributes=[DocumentAttributeFilename(file_name=filename)],
                        force_file=True,
                    ),
                )
            )
            doc = upload.document
            return InputDocument(
                id=doc.id,
                access_hash=doc.access_hash,
                file_reference=doc.file_reference,
            )

        finally:
            if temp_path and await temp_path.exists():
                await temp_path.unlink(missing_ok=True)

    async def add_sticker(
        self, sticker_data: str | BinaryIO | AsyncPath, set_name: str, emoji: str
    ):
        try:
            doc = await self.upload_media(sticker_data)
            await self.bot.client.invoke(
                AddStickerToSet(
                    stickerset=InputStickerSetShortName(short_name=set_name),
                    sticker=InputStickerSetItem(document=doc, emoji=emoji),
                )
            )
            return True, f"https://t.me/addstickers/{set_name}"
        except Exception as e:
            return False, f"Failed to add sticker: {e}"

    async def create_pack(
        self,
        sticker_data: str | BinaryIO | AsyncPath,
        set_name: str,
        set_title: str,
        emoji: str,
    ):
        try:
            doc = await self.upload_media(sticker_data)
            await self.bot.client.invoke(
                CreateStickerSet(
                    user_id=InputPeerSelf(),
                    title=set_title,
                    short_name=set_name,
                    stickers=[InputStickerSetItem(document=doc, emoji=emoji)],
                )
            )
            return True, f"https://t.me/addstickers/{set_name}"
        except Exception as e:
            return False, f"Failed to create sticker pack: {e}"

    @command.desc("Copy a sticker into another pack")
    @command.alias("stickercopy", "kang")
    @command.usage("[sticker pack VOL number? if not set] [emoji?]", optional=True)
    async def cmd_copysticker(self, ctx: command.Context) -> str:
        reply = ctx.msg.reply_to_message
        if not reply:
            return "__Reply to a sticker to copy it.__"
        if not reply.media:
            return "__Ewww can't kang that.__"

        await ctx.respond("__Preparing...__")

        vol, emoji, animated, video, resize = 1, None, False, False, False
        file = (
            reply.sticker
            or reply.photo
            or reply.video
            or reply.document
            or reply.animation
        )

        if reply.sticker:
            emoji = reply.sticker.emoji or emoji
            animated = reply.sticker.is_animated
            video = reply.sticker.is_video
            ext = Path(reply.sticker.file_name or "").suffix.lower()
            resize = ext not in [".tgs", ".webm"]
        elif reply.photo or (reply.document and "image" in reply.document.mime_type):
            resize = True
        elif reply.video:
            video, resize = True, True
        elif reply.document and "tgsticker" in reply.document.mime_type:
            animated = True
        elif reply.animation or (
            reply.document
            and "video" in reply.document.mime_type
            and reply.document.file_size <= MAX_VIDEO_SIZE
        ):
            video, resize = True, True

        for arg in ctx.args:
            emoji = arg if util.text.has_emoji(arg) else emoji
            if arg.isdigit():
                vol = int(arg)

        media_path = await reply.download()
        if not media_path:
            return "__Failed to download media.__"
        media = AsyncPath(media_path)

        if not animated and not video:
            mime = mimetypes.guess_type(str(media))[0]
            if mime and mime.startswith("video"):
                video = True
            elif mime == "application/x-tgsticker":
                animated = True

        username = self.bot.user.username
        uid = self.bot.user.id
        set_name = f"{username or uid}_kangPack_VOL{vol}"
        set_title = (
            f"@{username}'s Set VOL.{vol}" if username else f"{uid}'s Set VOL.{vol}"
        )

        if resize:
            try:
                media = await util.tools.resize_media_sticker(media, video)
            except FileNotFoundError:
                return (
                    "❌ [FFmpeg](https://github.com/FFmpeg/FFmpeg) is required.\n\n"
                    "If you're on Heroku:\n"
                    "[FFmpeg Buildpack](https://github.com/jonathanong/heroku-buildpack-ffmpeg-latest)"
                )
            if not await media.exists():
                return "__Failed to resize media.__"

        if animated:
            set_name += "_animation"
            set_title += " (Animation)"
        elif video:
            set_name += "_video"
            set_title += " (Video)"

        while True:
            try:
                sticker = await self.bot.client.invoke(
                    GetStickerSet(
                        stickerset=InputStickerSetShortName(short_name=set_name), hash=0
                    )
                )
            except StickersetInvalid:
                sticker = None
                break
            else:
                limit = 50 if (animated or video) else 120
                if sticker.set.count >= limit:
                    vol += 1
                    set_name = f"{username or uid}_kangPack_VOL{vol}"
                    set_title = (
                        f"@{username}'s Set VOL.{vol}"
                        if username
                        else f"{uid}'s Set VOL.{vol}"
                    )
                    if animated:
                        set_name += "_animation"
                        set_title += " (Animation)"
                    elif video:
                        set_name += "_video"
                        set_title += " (Video)"
                    await ctx.respond(
                        f"Pack VOL {vol - 1} full. Switching to VOL {vol}..."
                    )
                    continue
                break

        emoji = emoji or "❓"
        data = io.BytesIO(await media.read_bytes())
        data.name = media.name
        data.seek(0)

        await ctx.respond(
            "Creating sticker pack..." if not sticker else "__Copying sticker...__"
        )
        func = self.create_pack if not sticker else self.add_sticker
        success, result = await func(
            data, set_name, set_title if not sticker else emoji, emoji=emoji
        )

        await media.unlink(missing_ok=True)

        if success:
            await self.bot.log_stat("stickers_created")
            return f"[Sticker copied]({result})."
        return result
