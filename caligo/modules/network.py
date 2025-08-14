import asyncio
from datetime import datetime
from typing import Any, ClassVar, Literal, Optional, Set, Tuple

from aiopath import AsyncPath
from pyrogram import raw

from caligo import command, module, util


async def prog_func(
    current: int,
    total: int,
    start_time: int,
    mode: Literal["upload", "download"],
    ctx: command.Context,
    file_name: str,
) -> None:
    percent = current / total if total else 0
    elapsed = util.time.sec() - start_time
    now = datetime.now()

    try:
        speed = round(current / elapsed, 2) if elapsed else 0
        eta = (
            util.time.format_duration_td((total - current) / speed if speed else 0)
            if speed
            else "0s"
        )
    except ZeroDivisionError:
        speed = 0
        eta = "0s"

    bullets = "●" * int(round(percent * 10)) + "○"
    if len(bullets) > 10:
        bullets = bullets.replace("○", "")

    status = "Uploading" if mode == "upload" else "Downloading"
    space = "    " * (10 - len(bullets))
    progress = (
        f"`{file_name}`\n"
        f"Status: **{status}**\n"
        f"Progress: [{bullets + space}] {round(percent * 100)}%\n"
        f"__{util.misc.human_readable_bytes(current)} of {util.misc.human_readable_bytes(total)} @ "
        f"{util.misc.human_readable_bytes(speed, postfix='/s')}\n"
        f"eta - {eta}__\n\n"
    )

    if percent >= 1 or ctx.last_update_time is None:
        await ctx.respond(progress)
        ctx.last_update_time = now
        return

    if (now - ctx.last_update_time).total_seconds() >= 5:
        await ctx.respond(progress)
        ctx.last_update_time = now


class Network(module.Module):
    name: ClassVar[str] = "Network"
    tasks: Set[Tuple[int, asyncio.Task[Any]]]

    async def on_load(self) -> None:
        self.tasks = set()

    @command.desc("Pong")
    async def cmd_ping(self, ctx: command.Context):
        start = datetime.now()
        await self.bot.client.invoke(raw.functions.Ping(ping_id=0))
        latency = (datetime.now() - start).microseconds / 1000
        return f"Request response time: **{latency:.2f} ms**"

    @command.desc("Abort transmission of upload/download or PypDL task")
    @command.usage("[message progress to abort or GID]", reply=True)
    async def cmd_abort(self, ctx: command.Context) -> Optional[str]:
        if not ctx.input and not ctx.msg.reply_to_message:
            return "__Pass GID or reply to message of task to abort.__"

        if ctx.msg.reply_to_message and ctx.input:
            return "__Can't pass GID while replying to message.__"

        # Cancel PypDL task by GID
        if ctx.input:
            dl = self.bot.modules.get("PypDL")
            if dl and ctx.input in dl.mgr.downloads:
                t = dl.mgr.downloads[ctx.input]
                t.pypdl.stop()
                t.status = "Cancelled"
                dl.mgr.downloads.pop(ctx.input, None)  # remove immediately
                return f"__Cancelled PypDL task `{ctx.input}`.__"

        # Cancel Telegram media task
        reply_msg = ctx.msg.reply_to_message
        for msg_id, task in list(self.tasks.copy()):
            if (reply_msg and reply_msg.id == msg_id) or (
                ctx.input and ctx.input == str(msg_id)
            ):
                task.cancel()
                self.tasks.remove((msg_id, task))
                await ctx.msg.delete()
                return "__Transmission aborted.__"

        return "__The message/GID you choose is not in active tasks.__"

    @command.desc("Download from Telegram or HTTP/HTTPS (via PypDL)")
    @command.alias("dl")
    @command.usage("[HTTP URL or reply to media]", reply=True)
    async def cmd_download(self, ctx: command.Context) -> str:
        # HTTP/HTTPS → delegate to PypDL module
        if ctx.input and ctx.input.startswith(("http://", "https://")):
            dl = self.bot.modules.get("PypDL")
            if not dl:
                return "__PypDL module not available.__"
            res = await dl.add(ctx.input, ctx.msg)
            return res or "__HTTP download started.__"

        # Telegram media download
        if not ctx.msg.reply_to_message:
            return "__Reply to message with media to download or pass an HTTP URL.__"

        reply_msg = ctx.msg.reply_to_message
        if not reply_msg.media:
            return "__The replied message doesn't contain media.__"

        start_time = util.time.sec()
        await ctx.respond("Preparing to download...")

        try:
            media_group = await self.bot.client.get_media_group(
                ctx.chat.id, reply_msg.id
            )
        except ValueError:
            media_group = [reply_msg]

        results = set()
        for msg in media_group:
            media = getattr(msg, msg.media.value)
            try:
                base_name = media.file_name
            except AttributeError:
                base_name = f"{msg.media.value}_{(media.date or datetime.now()).strftime('%Y-%m-%d_%H-%M-%S')}"

            if "." in base_name:
                name, ext = base_name.rsplit(".", 1)
                name = f"{name}_{msg.id}.{ext}"
            else:
                name = f"{base_name}_{msg.id}"

            task = self.bot.loop.create_task(
                self.bot.client.download_media(
                    msg,
                    file_name=f"{ctx.msg._client.WORKDIR}/{self.bot.config.get('bot')['download_path']}/{name}",
                    progress=prog_func,
                    progress_args=(start_time, "download", ctx, name),
                )
            )
            self.tasks.add((ctx.msg.id, task))
            try:
                await task
            except asyncio.CancelledError:
                return "__Transmission aborted.__"
            else:
                self.tasks.remove((ctx.msg.id, task))
                results.add((msg.id, task.result()))

        path = ""
        for msg_id, result in results:
            if not result:
                path += f"__Failed to download media({msg_id}).__"
                continue
            if isinstance(result, str):
                path += f"\n× `{result}`"
            else:
                path += f"\n× `{result.name}`"

        if not path:
            return "__Failed to download media.__"

        return f"Downloaded to:\n{path}"

    @command.desc("Upload file into Telegram server")
    @command.alias("ul")
    @command.usage("[file path]")
    async def cmd_upload(self, ctx: command.Context) -> Optional[str]:
        if not ctx.input:
            return "__Pass the file path.__"

        start_time = util.time.sec()
        file_path = AsyncPath(ctx.input)

        if await file_path.is_dir():
            return "__The path you input is a directory.__"

        if not await file_path.is_file():
            return "__The file you input doesn't exists.__"

        await ctx.respond("Preparing to upload...")
        task = self.bot.loop.create_task(
            self.bot.client.send_document(
                ctx.msg.chat.id,
                str(file_path),
                message_thread_id=ctx.msg.message_thread_id,
                disable_content_type_detection=0,
                progress=prog_func,
                progress_args=(start_time, "upload", ctx, file_path.name),
            )
        )
        self.tasks.add((ctx.msg.id, task))
        try:
            await task
        except asyncio.CancelledError:
            return "__Transmission aborted.__"
        else:
            self.tasks.remove((ctx.msg.id, task))

        await ctx.msg.delete()
