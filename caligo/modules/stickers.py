import io
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, ClassVar

from aiopath import AsyncPath
from pyrogram import types
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


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w]+", "_", text)
    return text.strip("_")


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

    @command.desc("Copy a sticker to your own sticker pack")
    @command.alias("stickercopy", "kang")
    @command.usage("[emoji?] or --emoji=😊 --title='Custom Title'", optional=True)
    async def cmd_copysticker(self, ctx: command.Context) -> str:
        reply = ctx.msg.reply_to_message
        if not reply:
            return "__Reply to a sticker to copy it.__"
        if not reply.media:
            return "__This media can't be copied as a sticker.__"

        await ctx.respond("__Preparing...__")

        emoji, animated, video, resize = None, False, False, False
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

        custom_title = ctx.flags.get("title")

        for key, val in ctx.flags.items():
            if isinstance(key, str) and util.text.has_emoji(key):
                emoji = key
            if isinstance(val, str) and util.text.has_emoji(val):
                emoji = val

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
        user_prefix = username or str(uid)
        slug_base = slugify(custom_title) if custom_title else "kangpack"

        base_set_name = f"{user_prefix}_{slug_base}"
        set_title = custom_title or (
            f"@{username}'s Set" if username else f"{uid}'s Set"
        )

        suffix = 1
        set_name = f"{base_set_name}_{suffix}"

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
            base_set_name += "_animation"
            set_title += " (Animation)"
        elif video:
            base_set_name += "_video"
            set_title += " (Video)"

        while True:
            set_name = f"{base_set_name}_{suffix}"
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
                    suffix += 1
                    continue
                break

        emoji = emoji or "❓"
        data = io.BytesIO(await media.read_bytes())
        data.name = media.name
        data.seek(0)

        await ctx.respond(
            "Creating sticker pack..." if not sticker else "__Copying sticker...__"
        )

        if not sticker:
            success, result = await self.create_pack(data, set_name, set_title, emoji)
        else:
            success, result = await self.add_sticker(data, set_name, emoji)

        await media.unlink(missing_ok=True)

        if success:
            await self.bot.log_stat("stickers_created")
            await ctx.respond(
                f"[Sticker copied]({result}).",
                link_preview_options=types.LinkPreviewOptions(is_disabled=True),
            )
            return
        await ctx.respond(
            result, link_preview_options=types.LinkPreviewOptions(is_disabled=True)
        )

    @command.desc("Copy a full sticker set into your own")
    @command.usage(
        "source_pack [--merge=target_pack] [--title new_title] or reply to a sticker"
    )
    @command.alias("kangpack", "copystickerpack")
    async def cmd_copystickerset(self, ctx: command.Context) -> str:
        flags = ctx.flags
        reply = ctx.msg.reply_to_message

        source_name = None

        if reply and reply.sticker and reply.sticker.set_name:
            source_name = reply.sticker.set_name
        elif flags:
            source_name = next(iter(flags), None)

        if not source_name:
            return (
                "Usage: `kangpack <source_pack>` or reply to a sticker.\n"
                "Optional: `--merge=target_pack` or `--title new_title`"
            )

        merge_target = flags.get("merge")
        raw_title = flags.get("title")

        await ctx.respond("Fetching source sticker set...")

        try:
            result = await self.bot.client.invoke(
                GetStickerSet(
                    stickerset=InputStickerSetShortName(short_name=source_name),
                    hash=0,
                )
            )
        except Exception as e:
            return f"Failed to fetch source set: {e}"

        hash_map = {}
        for pack in result.packs:
            for doc_id in pack.documents:
                hash_map[doc_id] = pack.emoticon or "❓"

        items = []
        for doc in result.documents:
            items.append(
                InputStickerSetItem(
                    document=InputDocument(
                        id=doc.id,
                        access_hash=doc.access_hash,
                        file_reference=doc.file_reference,
                    ),
                    emoji=hash_map.get(doc.id, "❓"),
                )
            )

        if not items:
            return "Source sticker set has no stickers."

        if merge_target:
            await ctx.respond("Merging into existing sticker set...")

            try:
                existing = await self.bot.client.invoke(
                    GetStickerSet(
                        stickerset=InputStickerSetShortName(short_name=merge_target),
                        hash=0,
                    )
                )
            except Exception as e:
                return f"Failed to get target pack for merging: {e}"

            limit = 120
            if existing.set.count + len(items) > limit:
                return f"Target sticker set is too full to merge ({existing.set.count}/{limit})."

            success = 0
            for sticker in items:
                try:
                    await self.bot.client.invoke(
                        AddStickerToSet(
                            stickerset=InputStickerSetShortName(
                                short_name=merge_target
                            ),
                            sticker=sticker,
                        )
                    )
                    success += 1
                except Exception:
                    continue

            return ctx.respond(
                f"Merged {success}/{len(items)} stickers into [this pack](https://t.me/addstickers/{merge_target})."
            )

        # Generate name & title
        username = self.bot.user.username or str(self.bot.user.id)
        slug = slugify(raw_title) if raw_title else "pack"
        suffix = 1

        while True:
            candidate = f"{username}_{slug}_v{suffix}"
            try:
                await self.bot.client.invoke(
                    GetStickerSet(
                        stickerset=InputStickerSetShortName(short_name=candidate),
                        hash=0,
                    )
                )
                suffix += 1
            except StickersetInvalid:
                target_name = candidate
                break

        title = (
            f"{raw_title or 'Sticker Pack'} by @{username}"
            if self.bot.user.username
            else f"{raw_title or 'Sticker Pack'} by {self.bot.user.id}"
        )

        await ctx.respond("Creating new sticker set...")

        try:
            await self.bot.client.invoke(
                CreateStickerSet(
                    user_id=InputPeerSelf(),
                    title=title[:64],  # limit title to 64 chars
                    short_name=target_name,
                    stickers=items[:120],
                )
            )
        except Exception as e:
            return f"Failed to create sticker set: {e}"
        return ctx.respond(
            f"Sticker set copied successfully.\n[Click to open](https://t.me/addstickers/{target_name})"
        )
