import asyncio
import re
import shutil
import tempfile
import time
from html import escape
from typing import ClassVar

import instaloader
from aiopath import AsyncPath
from pyrogram.types import InputMediaPhoto, InputMediaVideo

from caligo import command, module


class SimpleRateController(instaloader.RateController):
    def __init__(self, context, sleep_time=2):
        super().__init__(context)
        self.sleep_time = sleep_time

    def sleep(self, secs):
        # Avoid blocking the event loop — Instaloader runs in thread
        time.sleep(secs)

    def query_waittime(self, query_type, current_time, untracked_queries=False):
        return self.sleep_time

    def handle_429(self, query_type):
        self.sleep(self.query_waittime(query_type, time.time()))

    def count_per_sliding_window(self, query_type):
        return 1


class InstaDL(module.Module):
    name: ClassVar = "InstaDL"

    async def on_load(self):
        self.loader = None
        self.downloads_dir = None
        self.session_file = None
        self.ig_user = None
        self.ig_pass = None
        self._session_state = None

        self.downloads_dir = AsyncPath(
            self.bot.config.get("bot", {}).get("download_path", "downloads")
        )
        await self.downloads_dir.mkdir(parents=True, exist_ok=True)

        self.session_file = AsyncPath("caligo/.cache/instagram_session")

        ig_cfg = self.bot.config.get("instagram", {})
        self.ig_user = ig_cfg.get("username")
        self.ig_pass = ig_cfg.get("password")

        self.loader = instaloader.Instaloader(
            download_videos=True,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
            post_metadata_txt_pattern="",
            max_connection_attempts=3,
            request_timeout=30,
            rate_controller=lambda ctx: SimpleRateController(ctx, 2),
            quiet=True,
        )

        if not self.ig_user or not self.ig_pass:
            self.bot.log.info(
                "Instagram: No credentials found — running in public mode."
            )
            self._session_state = "public"
            return

        if await self.session_file.exists():
            try:
                await asyncio.to_thread(
                    self.loader.load_session_from_file, None, str(self.session_file)
                )
                self.bot.log.info("Instagram: Loaded existing session file.")
                self._session_state = "session"
                return
            except Exception:
                self.bot.log.warning(
                    "Instagram: Session file invalid, logging in fresh."
                )

        try:
            await asyncio.to_thread(self.loader.login, self.ig_user, self.ig_pass)
            await asyncio.to_thread(
                self.loader.save_session_to_file, str(self.session_file)
            )
            self.bot.log.info("Instagram: Logged in and saved new session file.")
            self._session_state = "logged_in"
        except Exception as e:
            self.bot.log.error(f"Instagram login failed: {e}")
            try:
                if await self.session_file.exists():
                    await self.session_file.unlink()
            except Exception:
                pass
            self._session_state = "public"

    @staticmethod
    def _extract_shortcode(url: str) -> str | None:
        for pattern in (
            r"(?:https?://)?(?:www\.)?instagram\.com/p/([^/?#]+)",
            r"(?:https?://)?(?:www\.)?instagram\.com/reel/([^/?#]+)",
            r"(?:https?://)?(?:www\.)?instagram\.com/tv/([^/?#]+)",
            r"(?:https?://)?(?:www\.)?instagram\.com/stories/[^/]+/([^/?#]+)",
        ):
            if m := re.search(pattern, url):
                return m.group(1)
        return None

    async def _download_post(self, shortcode: str):
        temp_dir = self.downloads_dir / f"instagram_{shortcode}"
        await temp_dir.mkdir(parents=True, exist_ok=True)
        self.loader.dirname_pattern = str(temp_dir)

        post = await asyncio.to_thread(
            instaloader.Post.from_shortcode, self.loader.context, shortcode
        )

        caption = post.caption or ""
        if caption:
            caption = f"<blockquote expandable>{escape(caption)}</blockquote>"

        await asyncio.to_thread(self.loader.download_post, post, target="")

        media_files = [
            f
            async for f in temp_dir.glob("*")
            if f.suffix.lower() in {".mp4", ".jpg", ".jpeg", ".png"}
        ]
        if not media_files:
            raise ValueError("No media files found in downloaded content")

        return media_files, caption, temp_dir

    async def _send_album_chunks(
        self, chat_id, media_files, media_types, caption, reply_id
    ):
        MAX_ALBUM = 10
        for chunk_index in range(0, len(media_files), MAX_ALBUM):
            chunk_files = media_files[chunk_index : chunk_index + MAX_ALBUM]
            chunk_types = media_types[chunk_index : chunk_index + MAX_ALBUM]
            album = []
            for idx, file_path in enumerate(chunk_files):
                is_first = chunk_index == 0 and idx == 0
                if chunk_types[idx] == "video":
                    album.append(
                        InputMediaVideo(
                            file_path, caption=caption if is_first else None
                        )
                    )
                else:
                    album.append(
                        InputMediaPhoto(
                            file_path, caption=caption if is_first else None
                        )
                    )
            await self.bot.client.send_media_group(
                chat_id=chat_id, media=album, reply_to_message_id=reply_id
            )

    @command.desc("Download an Instagram video/reel/photo")
    @command.usage("--url <instagram link>")
    async def cmd_instadl(self, ctx: command.Context):
        url = ctx.flags.get("url") or ctx.input.strip()
        if not url:
            return "Please provide an Instagram URL using `--url`."

        await ctx.respond("Downloading ....")
        shortcode = self._extract_shortcode(url)

        media_files, media_types, caption, temp_dir = None, None, "", None
        try:
            if shortcode:
                files, caption, temp_dir = await self._download_post(shortcode)
                media_files = files
                media_types = [
                    "video" if f.suffix.lower() == ".mp4" else "image"
                    for f in media_files
                ]
            else:
                raise ValueError("Invalid Instagram URL format.")
        except Exception:
            api_url = f"https://api.ryzumi.vip/api/downloader/igdl?url={url}"
            async with self.bot.http.get(
                api_url, headers={"accept": "application/json"}
            ) as resp:
                if resp.status != 200:
                    return f"API failed with HTTP {resp.status}"
                data = await resp.json()

            if not data.get("status") or not data.get("data"):
                return "API returned no data"

            tmp_dir_obj = tempfile.TemporaryDirectory()
            tmp_dir_path = AsyncPath(tmp_dir_obj.name)
            media_files = []
            media_types = []

            for item in data["data"]:
                file_url = item.get("url")
                if not file_url:
                    continue

                file_type = item.get("type", "").lower()
                filename = file_url.split("?")[0].split("/")[-1]

                # Add extension if missing
                if "." not in filename:
                    filename += ".mp4" if file_type == "video" else ".jpg"

                tmp_path = tmp_dir_path / filename

                async with self.bot.http.get(file_url) as resp:
                    if resp.status != 200:
                        self.bot.log.warning(
                            f"Failed to download {file_url}: HTTP {resp.status}"
                        )
                        continue
                    await tmp_path.write_bytes(await resp.read())

                media_files.append(tmp_path)
                media_types.append(file_type)

            caption = (data["data"][0].get("caption") or "").strip()
            if caption:
                caption = f"<blockquote expandable>{escape(caption)}</blockquote>"

            temp_dir = tmp_dir_obj

        try:
            if len(media_files) == 1:
                if media_types[0] == "video":
                    await ctx.msg.edit_media(
                        InputMediaVideo(media_files[0], caption=caption)
                    )
                else:
                    await ctx.msg.edit_media(
                        InputMediaPhoto(media_files[0], caption=caption)
                    )
            else:
                await self._send_album_chunks(
                    ctx.chat.id, media_files, media_types, caption, ctx.msg.id
                )
                try:
                    await ctx.msg.delete()
                except Exception:
                    pass
        finally:
            if temp_dir:
                try:
                    if hasattr(temp_dir, "name"):
                        await asyncio.to_thread(shutil.rmtree, temp_dir.name)
                    else:
                        await asyncio.to_thread(shutil.rmtree, temp_dir)
                except Exception:
                    pass
