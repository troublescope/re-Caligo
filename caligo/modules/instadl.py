import asyncio
import mimetypes
import os
import re
import shutil
import tempfile
import uuid
from typing import ClassVar
from urllib.parse import parse_qs, unquote, urlparse

from aiopath import AsyncPath
from PIL import Image
from pyrogram import errors, filters, types

from caligo import command, listener, module, util


def extract_filename(download_url: str, index: int, content_type: str = None) -> str:
    parsed = urlparse(download_url)
    query = parse_qs(parsed.query)
    original_filename = unquote(query.get("filename", [f"media_{index}"])[0])

    ext = None
    if content_type:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
    if not ext and "." in parsed.path:
        ext = os.path.splitext(parsed.path)[1].lower()
    if not ext and "." in original_filename:
        ext = os.path.splitext(original_filename)[1].lower()
    if not ext:
        ext = ".jpg"
    if "?" in ext:
        ext = ext.split("?")[0]
    if not ext.startswith("."):
        ext = "." + ext
    return f"media_{index}{ext}"


# Regex that matches full Instagram URLs
INSTAGRAM_REGEX = r"(https?://(?:www\.)?(?:instagram\.com|instagr\.am)/[^\s]+)"


class InstaDL(module.Module):
    name: ClassVar = "InstaDL"

    async def on_load(self):
        self.downloads_dir = AsyncPath(
            self.bot.config.get("bot", {}).get("download_path", "downloads")
        )
        await self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self._cache = {}

    async def fetch_media(self, url: str):
        api_url = "https://fastdl.live/api/search"
        payload = {"url": url}

        async with self.bot.http.post(
            api_url, json=payload, headers={"Content-Type": "application/json"}
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"API failed HTTP {resp.status}")
            data = await resp.json()

        if not data.get("success") or not data.get("result"):
            raise ValueError("No media found or invalid URL")

        media_list = []
        for idx, item in enumerate(data["result"], start=1):
            media_list.append(
                {
                    "id": idx,
                    "type": item["type"].lower(),
                    "downloadLink": item["downloadLink"],
                    "temp_filename": f"media_{idx}_temp",
                }
            )
        return media_list

    async def download_media(self, media_list):
        tmp_dir_obj = tempfile.TemporaryDirectory()
        tmp_dir = AsyncPath(tmp_dir_obj.name)

        async def download_item(item):
            url = item["downloadLink"]
            file_type = item["type"]
            temp_path = tmp_dir / item["temp_filename"]

            try:
                async with self.bot.http.get(url) as resp:
                    if resp.status != 200:
                        self.bot.log.warning(
                            f"Failed to download {url}: HTTP {resp.status}"
                        )
                        return None, None
                    content_bytes = await resp.read()
                    await temp_path.write_bytes(content_bytes)
            except Exception as e:
                self.bot.log.warning(f"Error downloading {url}: {e}")
                return None, None

            if file_type == "video":
                final_path = tmp_dir / f"media_{item['id']}.mp4"
                await temp_path.rename(final_path)
            else:
                final_path = tmp_dir / f"media_{item['id']}.jpg"
                try:
                    img = await util.run_sync(Image.open, str(temp_path))
                    img = img.convert("RGB")
                    await util.run_sync(img.save, str(final_path), "JPEG")
                    await temp_path.unlink(missing_ok=True)
                except Exception as e:
                    self.bot.log.warning(f"Failed to convert image {temp_path}: {e}")
                    await temp_path.rename(final_path)

            return final_path, file_type

        results = await asyncio.gather(*(download_item(item) for item in media_list))
        media_files, media_types = zip(*[r for r in results if r and r[0]])
        return list(media_files), list(media_types), tmp_dir_obj

    async def cache_files(self, media_files, media_types):
        async def cache_item(path, file_type):
            try:
                if file_type == "video":
                    msg = await self.bot.client_helper.send_video(
                        self.bot.log_chat, video=str(path), disable_notification=True
                    )
                    file_id = msg.video.file_id
                else:
                    msg = await self.bot.client_helper.send_photo(
                        self.bot.log_chat, photo=str(path), disable_notification=True
                    )
                    file_id = msg.photo.file_id
                try:
                    await msg.delete()
                except errors.FloodWait as e:
                    await asyncio.sleep(e.value)
                return file_id
            except Exception as e:
                self.bot.log.warning(f"Failed to cache {path}: {e}")
                return None

        return await asyncio.gather(
            *(cache_item(path, t) for path, t in zip(media_files, media_types))
        )

    def build_nav_buttons(self, uid: str, index: int, total: int):
        prev_index = (index - 1) % total
        next_index = (index + 1) % total

        row1 = [
            types.InlineKeyboardButton(
                "«", callback_data=f"instadl({uid}:{prev_index})"
            ),
            types.InlineKeyboardButton(f"{index+1}/{total}", callback_data="noop"),
            types.InlineKeyboardButton(
                "»", callback_data=f"instadl({uid}:{next_index})"
            ),
        ]
        row2 = [types.InlineKeyboardButton("✗ Close", callback_data="menu(Close)")]
        return types.InlineKeyboardMarkup([row1, row2])

    @listener.priority(91)
    @listener.filters(filters.regex(r"^instadl:"))
    async def on_inline_query(self, inline_query: types.InlineQuery):
        try:
            _, uid, idx = inline_query.query.split(":")
            idx = int(idx)
        except Exception:
            return await inline_query.answer(
                [], switch_pm_text="Invalid query", switch_pm_parameter="err"
            )

        cache = self._cache.get(uid)
        if not cache:
            return await inline_query.answer(
                [], switch_pm_text="Expired ⌛", switch_pm_parameter="exp"
            )

        file_ids, media_types = cache["file_ids"], cache["types"]

        results = []
        if media_types[idx] == "video":
            results.append(
                types.InlineQueryResultCachedVideo(
                    id=str(uuid.uuid4()),
                    video_file_id=file_ids[idx],
                    title="Instagram Video",
                    description=f"{idx+1}/{len(file_ids)}",
                    reply_markup=self.build_nav_buttons(uid, idx, len(file_ids)),
                )
            )
        else:
            results.append(
                types.InlineQueryResultCachedPhoto(
                    id=str(uuid.uuid4()),
                    photo_file_id=file_ids[idx],
                    title="Instagram Photo",
                    description=f"{idx+1}/{len(file_ids)}",
                    reply_markup=self.build_nav_buttons(uid, idx, len(file_ids)),
                )
            )

        await inline_query.answer(results, cache_time=0, is_personal=True)

    @listener.filters(filters.regex(r"^instadl"))
    async def on_callback_query(self, query: types.CallbackQuery):
        data = query.data

        if data.startswith("instadl("):
            try:
                uid, idx = data[8:-1].split(":")
                idx = int(idx)
            except Exception:
                return await query.answer("Invalid data", show_alert=True)

            cache = self._cache.get(uid)
            if not cache:
                return await query.answer("Expired ⌛", show_alert=True)

            file_ids, media_types = cache["file_ids"], cache["types"]
            total = len(file_ids)

            try:
                if media_types[idx] == "video":
                    media = types.InputMediaVideo(file_ids[idx])
                else:
                    media = types.InputMediaPhoto(file_ids[idx])

                await query.edit_message_media(
                    media, reply_markup=self.build_nav_buttons(uid, idx, total)
                )
            except errors.MessageNotModified:
                pass
            except errors.FloodWait as e:
                await asyncio.sleep(e.value)

    @command.desc("Download Instagram video/reel/photo (supports multipost)")
    @command.usage("<instagram link> [-i for inline mode]")
    async def cmd_instadl(self, ctx: command.Context):
        text = ctx.input.strip()
        inline_mode = "-i" in ctx.flags or ctx.flags.get("i") is True

        await ctx.respond("...")

        match = re.search(INSTAGRAM_REGEX, text)
        if not match:
            return "Please provide a valid Instagram URL."

        url = match.group(1)

        tmp_dir_obj = None
        try:
            media_list = await self.fetch_media(url)
            media_files, media_types, tmp_dir_obj = await self.download_media(
                media_list
            )

            # === INLINE MODE ===
            if inline_mode:
                if len(media_files) == 1:
                    if media_types[0] == "video":
                        await ctx.msg.edit_media(
                            types.InputMediaVideo(str(media_files[0]))
                        )
                    else:
                        await ctx.msg.edit_media(
                            types.InputMediaPhoto(str(media_files[0]))
                        )
                    return

                file_ids = await self.cache_files(media_files, media_types)
                uid = str(uuid.uuid4())
                self._cache[uid] = {
                    "file_ids": file_ids,
                    "types": media_types,
                    "media_files": media_files,
                    "tmp_dir_obj": tmp_dir_obj,
                }

                results = await self.bot.client.get_inline_bot_results(
                    self.bot.client_helper.me.username, f"instadl:{uid}:0"
                )

                await self.bot.client.send_inline_bot_result(
                    ctx.chat.id, results.query_id, results.results[0].id
                )
                await ctx.msg.delete()
                return

            album = []
            for idx, path in enumerate(media_files):
                if media_types[idx] == "video":
                    album.append(types.InputMediaVideo(media=str(path)))
                else:
                    album.append(types.InputMediaPhoto(media=str(path)))

            if not album:
                return "No valid media to send."

            if len(album) == 1:
                await ctx.msg.edit_media(album[0])
            else:
                await self.bot.client.send_media_group(ctx.chat.id, album)
                await ctx.msg.delete()

        except Exception as e:
            return f"Error: {e}"

        finally:
            if tmp_dir_obj:
                try:
                    await asyncio.to_thread(shutil.rmtree, tmp_dir_obj.name)
                except Exception:
                    pass
