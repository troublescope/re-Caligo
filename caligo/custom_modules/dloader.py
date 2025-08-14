from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, ClassVar, Dict, Optional, Union

import pyrogram
from aiopath import AsyncPath
from pypdl import Pypdl
from pyrogram.errors import FloodWait, MessageNotModified

from .. import module, util


class DownloadTask:
    """Represents a single download task."""

    def __init__(self, gid: str, p: Pypdl, path: AsyncPath) -> None:
        self.gid = gid
        self.pypdl = p
        self.path = path
        self.status = "Downloading"
        self.error: Optional[str] = None


class PypdlManager:
    log: ClassVar[logging.Logger] = logging.getLogger("PypdlManager")

    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self.downloads: Dict[str, DownloadTask] = {}
        self.invoker: Optional[pyrogram.types.Message] = None
        self.stopping = False

    def _bar(self, pct: float) -> str:
        if pct != pct or pct is None:
            pct = 0.0
        pct = max(0.0, min(100.0, float(pct)))
        filled = int(round(pct / 10.0))
        filled = max(0, min(10, filled))
        return "●" * filled + "○" * (10 - filled)

    async def _progress_text(self) -> str:
        human = util.misc.human_readable_bytes
        lines: list[str] = []

        for t in list(self.downloads.values()):
            p = t.pypdl
            try:
                done_b = int(p.current_size or 0)
                total_b = int(p.size or 0)
                pct = float(p.progress or 0)
                speed_b = int((p.speed or 0) * 1024 * 1024)  # MB/s → B/s

                if p.eta and p.eta > 0:
                    eta = str(timedelta(seconds=int(p.eta)))
                elif total_b == 0:
                    eta = "Fetching size..."
                else:
                    eta = "—"
            except Exception as e:
                t.status, t.error = "Failed", str(e)
                pct = 0.0
                done_b = total_b = speed_b = 0
                eta = "—"

            if p.completed and t.status == "Downloading":
                t.status = "Complete"

            # Remove cancelled or failed tasks from view and delete file
            if t.status in ("Cancelled", "Failed"):
                try:
                    if await t.path.exists():
                        await t.path.unlink()
                except Exception as e:
                    self.log.warning("Failed to remove file %s: %s", t.path, e)
                self.downloads.pop(t.gid, None)
                continue

            lines.append(
                f"`{t.path.name}`\n"
                f"GID: `{t.gid}`\n"
                f"Status: **{t.status}**\n"
                f"Progress: [{self._bar(pct)}] {int(pct)}%\n"
                f"__{human(done_b)} of {human(total_b)} @ {human(speed_b, postfix='/s')}\n"
                f"ETA: {eta}__\n"
            )

        return "\n".join(lines)

    async def update_loop(self) -> None:
        last_update = datetime.now() - timedelta(seconds=10)
        while not self.stopping:
            if not self.downloads:
                # Delete progress message if nothing to show
                if self.invoker:
                    try:
                        await self.invoker.delete()
                    except Exception:
                        pass
                    self.invoker = None
                await asyncio.sleep(0.5)
                continue

            text = await self._progress_text()
            now = datetime.now()
            if text and (now - last_update).total_seconds() >= 5:
                if self.invoker:
                    try:
                        await self.bot.respond(self.invoker, text)
                    except MessageNotModified:
                        pass
                    except FloodWait as e:
                        await asyncio.sleep(e.value)
                    except Exception as e:
                        self.log.exception("Failed to send progress update: %s", e)
                last_update = now
            await asyncio.sleep(0.2)


class PypDL(module.Module):
    """HTTP/HTTPS downloader using pypdl."""

    name: ClassVar[str] = "PypDL"
    mgr: PypdlManager

    async def on_start(self, _: int) -> None:
        self.mgr = PypdlManager(self.bot)
        path = AsyncPath(self.bot.config["bot"]["download_path"])
        await path.mkdir(parents=True, exist_ok=True)
        self.bot.loop.create_task(self.mgr.update_loop(), name="pypdl-progress")

    async def on_stop(self) -> None:
        self.mgr.stopping = True
        for t in list(self.mgr.downloads.values()):
            try:
                t.pypdl.stop()
            except Exception:
                pass
        self.mgr.downloads.clear()
        self.mgr.invoker = None

    async def add(
        self, url: Union[str, bytes], msg: pyrogram.types.Message
    ) -> Optional[str]:
        if isinstance(url, bytes):
            return "__Torrent/magnet not supported.__"
        if not (isinstance(url, str) and url.startswith(("http://", "https://"))):
            return "__Only HTTP/HTTPS URLs supported.__"

        base = AsyncPath(self.bot.config["bot"]["download_path"])
        name = util.misc.get_filename_from_url(url) or f"dl-{uuid.uuid4().hex[:8]}"
        filepath = base / util.misc.sanitize_filename(name)

        p = Pypdl()
        gid = uuid.uuid4().hex[:8]
        try:
            p.start(
                url=url,
                file_path=str(filepath),
                segments=10,
                multisegment=True,
                retries=2,
                speed_limit=0,
                etag_validation=True,
                block=False,
                display=False,
                headers=None,
                auth=None,
                timeout=300,
            )
        except Exception as e:
            # Remove partial file if exists
            try:
                if await filepath.exists():
                    await filepath.unlink()
            except Exception as ex:
                self.mgr.log.warning(
                    "Failed to remove failed file %s: %s", filepath, ex
                )
            self.mgr.downloads.pop(gid, None)
            return f"__Failed__: {e}"

        self.mgr.downloads[gid] = DownloadTask(gid, p, filepath)
        if self.mgr.invoker:
            try:
                await self.mgr.invoker.delete()
            except Exception:
                pass
        self.mgr.invoker = msg
        return None

    async def pause(self, gid: str) -> Dict[str, Any]:
        t = self.mgr.downloads.get(gid)
        if not t:
            return {"ok": False, "message": "__GID not found.__"}
        try:
            t.pypdl.stop()
            t.status = "Paused"
            return {"ok": True, "message": "__Paused.__"}
        except Exception as e:
            return {"ok": False, "message": f"__Pause failed__: {e}"}

    async def removeDownload(self, gid: str) -> Dict[str, Any]:
        t = self.mgr.downloads.get(gid)
        if not t:
            return {"ok": False, "message": "__GID not found.__"}
        try:
            t.pypdl.stop()
            t.status = "Cancelled"
            self.mgr.downloads.pop(gid, None)
            return {"ok": True, "message": "__Cancelled.__"}
        except Exception as e:
            return {"ok": False, "message": f"__Cancel failed__: {e}"}

    async def cancel(self, gid: str) -> Optional[str]:
        res = await self.removeDownload(gid)
        return None if res.get("ok") else res.get("message")

    async def resume(self, gid: str) -> Dict[str, Any]:
        t = self.mgr.downloads.get(gid)
        if not t or t.status != "Paused":
            return {"ok": False, "message": "__Not paused or GID not found.__"}
        try:
            t.pypdl.start(
                url=t.pypdl.url,
                file_path=str(t.path),
                segments=10,
                multisegment=True,
                retries=2,
                speed_limit=0,
                etag_validation=True,
                block=False,
                display=False,
                headers=None,
                auth=None,
                timeout=300,
            )
            t.status = "Downloading"
            return {"ok": True, "message": "__Resumed.__"}
        except Exception as e:
            return {"ok": False, "message": f"__Resume failed__: {e}"}
