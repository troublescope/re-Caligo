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
        self.start_time = datetime.now()  # For our own ETA calc


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
                done_b = int(getattr(p, "current_size", 0) or 0)
                total_b = int(getattr(p, "size", 0) or 0)

                # Progress %
                raw_pct = getattr(p, "progress", None)
                try:
                    pct = float(raw_pct) if raw_pct is not None else None
                except Exception:
                    pct = None
                if (pct is None or pct <= 1) and total_b > 0:
                    pct = (done_b / total_b) * 100.0
                if pct is None:
                    pct = 0.0
                pct = max(0.0, min(100.0, pct))

                # Speed (convert KB/s to B/s if needed)
                raw_speed = getattr(p, "speed", 0) or 0
                try:
                    raw_speed = float(raw_speed)
                except Exception:
                    raw_speed = 0.0
                if raw_speed > 10000:
                    speed_bps = raw_speed
                else:
                    speed_bps = raw_speed * 1024.0
                speed_b = int(speed_bps)

                # Custom ETA calculation
                elapsed = (datetime.now() - t.start_time).total_seconds()
                if done_b > 0 and elapsed > 0:
                    avg_speed = done_b / elapsed
                    if avg_speed > 0 and total_b > done_b:
                        remaining = total_b - done_b
                        eta = str(timedelta(seconds=int(remaining / avg_speed)))
                    else:
                        eta = "—"
                else:
                    eta = "Starting..."

            except Exception as e:
                t.status, t.error = "Failed", str(e)
                pct = 0.0
                done_b = total_b = speed_b = 0
                eta = "—"

            if getattr(p, "completed", False) and t.status == "Downloading":
                t.status = "Complete"

            if t.status in ("Cancelled", "Failed"):
                self.downloads.pop(t.gid, None)

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
                        await asyncio.sleep(e.x)
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
            return f"__Failed__: {e}"

        gid = uuid.uuid4().hex[:8]
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
            t.start_time = datetime.now()  # reset ETA timer
            t.pypdl.start(
                url=getattr(t.pypdl, "url", None),
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
