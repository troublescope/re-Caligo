import asyncio
import re
import shutil
import tempfile
import uuid
from typing import ClassVar

from aiopath import AsyncPath
from PIL import Image
from pyrogram import errors, filters, types

from caligo import command, listener, module, util


class InstaDL(module.Module):
    name: ClassVar[str] = "InstaDL"
    INSTAGRAM_REGEX: ClassVar[str] = (
        r"(https?://(?:www\.)?(?:instagram\.com|instagr\.am)/[^\s]+)"
    )
    API_URL: ClassVar[str] = "https://fastdl.live/api/search"

    async def on_load(self):
        self.downloads_dir = AsyncPath(
            self.bot.config.get("bot", {}).get("download_path", "downloads")
        )
        await self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}

    async def _fetch_media(self, url: str) -> list[dict]:
        payload = {"url": url}
        async with self.bot.http.post(
            self.API_URL, json=payload, headers={"Content-Type": "application/json"}
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"API failed HTTP {resp.status}")
            data = await resp.json()
        if not data.get("success") or not data.get("result"):
            raise ValueError("No media found or invalid URL")
        return [
            {
                "id": i,
                "type": item["type"].lower(),
                "downloadLink": item["downloadLink"],
                "tmp": f"media_{i}_tmp",
            }
            for i, item in enumerate(data["result"], start=1)
        ]

    async def _download_media(self, items: list[dict]):
        tmp_obj = tempfile.TemporaryDirectory()
        tmp_dir = AsyncPath(tmp_obj.name)

        async def dl(item: dict):
            url = item["downloadLink"]
            path_tmp = tmp_dir / item["tmp"]
            try:
                async with self.bot.http.get(url) as r:
                    if r.status != 200:
                        self.bot.log.warning(f"DL fail {url}: {r.status}")
                        return None
                    await path_tmp.write_bytes(await r.read())
            except Exception as e:
                self.bot.log.warning(f"DL err {url}: {e}")
                return None

            ftype = item["type"]
            final = (
                tmp_dir / f"media_{item['id']}{'.mp4' if ftype == 'video' else '.jpg'}"
            )
            if ftype == "video":
                await path_tmp.rename(final)
            else:
                try:
                    img = await util.run_sync(Image.open, str(path_tmp))
                    img = img.convert("RGB")
                    await util.run_sync(img.save, str(final), "JPEG")
                    await path_tmp.unlink(missing_ok=True)
                except Exception as e:
                    self.bot.log.warning(f"IMG convert fail {path_tmp}: {e}")
                    await path_tmp.rename(final)
            return str(final), ftype

        results = [r for r in await asyncio.gather(*(dl(x) for x in items)) if r]
        files, types_ = zip(*results) if results else ([], [])
        return list(files), list(types_), tmp_obj

    async def _cache_files(
        self, files: list[str], types_: list[str]
    ) -> list[str | None]:
        async def cache_one(path: str, t: str):
            try:
                if t == "video":
                    msg = await self.bot.client_helper.send_video(
                        self.bot.log_chat, video=path, disable_notification=True
                    )
                    fid = msg.video.file_id
                else:
                    msg = await self.bot.client_helper.send_photo(
                        self.bot.log_chat, photo=path, disable_notification=True
                    )
                    fid = msg.photo.file_id
                try:
                    await msg.delete()
                except errors.FloodWait as e:
                    await asyncio.sleep(e.value)
                return fid
            except Exception as e:
                self.bot.log.warning(f"Cache fail {path}: {e}")
                return None

        return await asyncio.gather(*(cache_one(p, t) for p, t in zip(files, types_)))

    def _nav(self, uid: str, idx: int, total: int) -> types.InlineKeyboardMarkup:
        prev_i = (idx - 1) % total
        next_i = (idx + 1) % total
        row1 = [
            types.InlineKeyboardButton("«", callback_data=f"instadl({uid}:{prev_i})"),
            types.InlineKeyboardButton(f"{idx+1}/{total}", callback_data="noop"),
            types.InlineKeyboardButton("»", callback_data=f"instadl({uid}:{next_i})"),
        ]
        row2 = [types.InlineKeyboardButton("✗ Close", callback_data="menu(Close)")]
        return types.InlineKeyboardMarkup([row1, row2])

    @staticmethod
    def _chunks(seq, n):
        for i in range(0, len(seq), n):
            yield seq[i : i + n]

    async def _send_album_chunked(self, chat_id: int, album: list[types.InputMedia]):
        for chunk in self._chunks(album, 10):
            if len(chunk) == 1:
                m = chunk[0]
                try:
                    if isinstance(m, types.InputMediaVideo):
                        await self.bot.client.send_video(
                            chat_id,
                            m.media,
                            caption=m.caption,
                            caption_entities=m.caption_entities,
                        )
                    else:
                        await self.bot.client.send_photo(
                            chat_id,
                            m.media,
                            caption=m.caption,
                            caption_entities=m.caption_entities,
                        )
                except errors.FloodWait as e:
                    await asyncio.sleep(e.value)
                    if isinstance(m, types.InputMediaVideo):
                        await self.bot.client.send_video(
                            chat_id,
                            m.media,
                            caption=m.caption,
                            caption_entities=m.caption_entities,
                        )
                    else:
                        await self.bot.client.send_photo(
                            chat_id,
                            m.media,
                            caption=m.caption,
                            caption_entities=m.caption_entities,
                        )
                continue
            try:
                await self.bot.client.send_media_group(chat_id, chunk)
            except errors.FloodWait as e:
                await asyncio.sleep(e.value)
                await self.bot.client.send_media_group(chat_id, chunk)

    @listener.priority(91)
    @listener.filters(filters.regex(r"^instadl:"))
    async def on_inline_query(self, q: types.InlineQuery):
        try:
            _, uid, idx = q.query.split(":")
            idx = int(idx)
        except Exception:
            return await q.answer(
                [], switch_pm_text="Invalid query", switch_pm_parameter="err"
            )

        cache = self._cache.get(uid)
        if not cache:
            return await q.answer(
                [], switch_pm_text="Expired ⌛", switch_pm_parameter="exp"
            )

        fids, types_ = cache["file_ids"], cache["types"]
        if not fids or idx >= len(fids):
            return await q.answer(
                [], switch_pm_text="No media", switch_pm_parameter="nomedia"
            )

        res = []
        if types_[idx] == "video":
            res.append(
                types.InlineQueryResultCachedVideo(
                    id=str(uuid.uuid4()),
                    video_file_id=fids[idx],
                    title="Instagram Video",
                    description=f"{idx+1}/{len(fids)}",
                    reply_markup=self._nav(uid, idx, len(fids)),
                )
            )
        else:
            res.append(
                types.InlineQueryResultCachedPhoto(
                    id=str(uuid.uuid4()),
                    photo_file_id=fids[idx],
                    title="Instagram Photo",
                    description=f"{idx+1}/{len(fids)}",
                    reply_markup=self._nav(uid, idx, len(fids)),
                )
            )
        await q.answer(res, cache_time=0, is_personal=True)

    @listener.filters(filters.regex(r"^instadl"))
    async def on_callback_query(self, cq: types.CallbackQuery):
        if not cq.data.startswith("instadl("):
            return
        try:
            uid, idx = cq.data[8:-1].split(":")
            idx = int(idx)
        except Exception:
            return await cq.answer("Invalid data", show_alert=True)

        cache = self._cache.get(uid)
        if not cache:
            return await cq.answer("Expired ⌛", show_alert=True)

        fids, types_ = cache["file_ids"], cache["types"]
        total = len(fids)
        if not total:
            return await cq.answer("No media", show_alert=True)

        try:
            media = (
                types.InputMediaVideo(fids[idx])
                if types_[idx] == "video"
                else types.InputMediaPhoto(fids[idx])
            )
            await cq.edit_message_media(media, reply_markup=self._nav(uid, idx, total))
        except errors.MessageNotModified:
            pass
        except errors.FloodWait as e:
            await asyncio.sleep(e.value)

    @command.desc("Download Instagram video/reel/photo (supports multipost)")
    @command.usage("<instagram link> [-i for inline mode]")
    async def cmd_instadl(self, ctx: command.Context):
        txt = ctx.input.strip()
        inline_mode = "-i" in ctx.flags or ctx.flags.get("i") is True

        await ctx.respond("...")

        m = re.search(self.INSTAGRAM_REGEX, txt)
        if not m:
            return "Please provide a valid Instagram URL."
        url = m.group(1)

        tmp_obj = None
        try:
            items = await self._fetch_media(url)
            files, types_, tmp_obj = await self._download_media(items)

            if inline_mode:
                if len(files) == 1:
                    media = (
                        types.InputMediaVideo(files[0])
                        if types_[0] == "video"
                        else types.InputMediaPhoto(files[0])
                    )
                    await ctx.msg.edit_media(media)
                    return
                fids = await self._cache_files(files, types_)
                uid = str(uuid.uuid4())
                self._cache[uid] = {"file_ids": fids, "types": types_, "tmp": tmp_obj}
                results = await self.bot.client.get_inline_bot_results(
                    self.bot.client_helper.me.username, f"instadl:{uid}:0"
                )
                await self.bot.client.send_inline_bot_result(
                    ctx.chat.id, results.query_id, results.results[0].id
                )
                await ctx.msg.delete()
                return

            album: list[types.InputMedia] = [
                (
                    types.InputMediaVideo(media=f)
                    if types_[i] == "video"
                    else types.InputMediaPhoto(media=f)
                )
                for i, f in enumerate(files)
            ]
            if not album:
                return "No valid media to send."

            if len(album) == 1:
                await ctx.msg.edit_media(album[0])
            else:
                await self._send_album_chunked(ctx.chat.id, album)
                await ctx.msg.delete()

        except Exception as e:
            return f"Error: {e}"
        finally:
            if tmp_obj:
                try:
                    await asyncio.to_thread(shutil.rmtree, tmp_obj.name)
                except Exception:
                    pass
