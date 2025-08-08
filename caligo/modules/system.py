import asyncio
import os
import platform
import sys
from html import escape
from typing import Any, ClassVar, Mapping, Optional

import speedtest
from aiopath import AsyncPath
from pymongo.asynchronous.collection import AsyncCollection
from pyrogram import enums
from pyrogram.enums import ParseMode
from pyrogram.types import Message

from caligo import __version__, command, module, util


class System(module.Module):
    name: ClassVar[str] = "System"

    db: AsyncCollection
    restart_pending: bool

    async def on_load(self):
        self.restart_pending = False
        self.repo = self.bot.config["bot"]["git_url"]

        self.db = self.bot.db.get_collection(self.name.upper())

    async def on_start(self, time_us: int) -> None:  # skipcq: PYL-W0613
        # Update restart status message if applicable
        data: Optional[Mapping[str, Mapping[str, Any]]] = await self.db.find_one(
            {"_id": 0}
        )
        if data is not None:
            restart = data["restart"]
            # Fetch status message info
            rs_time: Optional[int] = restart.get("time")
            rs_chat_id: Optional[int] = restart.get("status_chat_id")
            rs_message_id: Optional[int] = restart.get("status_message_id")
            rs_thread_id: int = restart.get("status_thread_id")  # type: ignore
            rs_reason: Optional[str] = restart.get("reason")

            # Delete DB keys first in case message editing fails
            await self.db.delete_one({"_id": 0})

            # Bail out if we're missing necessary values
            if rs_chat_id is None or rs_message_id is None or rs_time is None:
                return

            # Show message
            updated = "updated and " if rs_reason == "update" else ""
            duration = util.time.format_duration_us(util.time.usec() - rs_time)
            self.log.info("Bot %srestarted in %s", updated, duration)

            status_msg: Message = await self.bot.client.get_messages(
                rs_chat_id, rs_message_id
            )  # type: ignore
            try:
                await self.bot.respond(
                    status_msg, f"Bot {updated}restarted in {duration}.", mode="repost"
                )
            except AttributeError:
                await self.bot.client.send_message(
                    rs_chat_id,
                    f"Bot {updated}restarted in {duration}.",
                    message_thread_id=rs_thread_id,
                )

    async def on_stopped(self) -> None:
        if self.restart_pending:
            self.log.info("Starting new bot instance...\n")
            # This is safe because original arguments are reused. skipcq: BAN-B606
            os.execv(sys.executable, (sys.executable, "-m", "caligo"))

    @command.desc("Stop this bot")
    async def cmd_stop(self, ctx: command.Context) -> None:
        await ctx.respond("Stopping bot...")
        self.bot.__idle__.cancel()

    @command.desc("Restart this bot")
    @command.alias("re", "rst")
    async def cmd_restart(
        self,
        ctx: command.Context,
        *,
        restart_time: Optional[int] = None,
        reason="manual",
    ) -> None:
        resp_msg = await ctx.respond("Restarting bot...")

        # Save time and status message so we can update it after restarting
        await self.db.update_one(
            {"_id": 0},
            {
                "$set": {
                    "restart.status_chat_id": resp_msg.chat.id,
                    "restart.status_message_id": resp_msg.id,
                    "restart.status_thread_id": resp_msg.message_thread_id,
                    "restart.time": restart_time or util.time.usec(),
                    "restart.reason": reason,
                }
            },
            upsert=True,
        )
        # Initiate the restart
        self.restart_pending = True
        self.log.info("Preparing to restart...")
        self.bot.__idle__.cancel()

    @command.desc("Test Internet speed")
    @command.alias("stest")
    async def cmd_speedtest(self, ctx: command.Context) -> str:
        before = util.time.usec()

        st = await util.run_sync(speedtest.Speedtest)
        status = "Selecting server..."

        await ctx.respond(status)
        server = await util.run_sync(st.get_best_server)
        status += f" {server['sponsor']} ({server['name']})\n"
        status += f"Ping: {server['latency']:.2f} ms\n"

        status += "Performing download test..."
        await ctx.respond(status)
        dl_bits = await util.run_sync(st.download)
        dl_mbit = dl_bits / 1000 / 1000
        status += f" {dl_mbit:.2f} Mbps\n"

        status += "Performing upload test..."
        await ctx.respond(status)
        ul_bits = await util.run_sync(st.upload)
        ul_mbit = ul_bits / 1000 / 1000
        status += f" {ul_mbit:.2f} Mbps\n"

        delta = util.time.usec() - before
        status += f"\nTime elapsed: {util.time.format_duration_us(delta)}"

        return status

    @command.desc("Run a snippet in a shell")
    @command.usage("[shell snippet]")
    @command.alias("sh")
    async def cmd_shell(self, ctx: command.Context) -> Optional[str]:
        snip = ctx.input
        if not snip:
            return "Give me command to run."

        await ctx.respond("Running snippet...")
        before = util.time.usec()

        try:
            stdout, _, ret = await util.system.run_command(
                snip, shell=True, timeout=120  # skipcq: BAN-B604
            )
        except FileNotFoundError as E:
            after = util.time.usec()
            await ctx.respond(
                f"""<b>Input</b>:<pre language="bash">{escape(snip)}</pre>
<b>Output</b>:
⚠️ Error executing command:
<pre language="bash">{escape(util.error.format_exception(E))}</pre>

f"Time: {util.time.format_duration_us(after - before)}""",
                parse_mode=ParseMode.HTML,
            )
            return
        except asyncio.TimeoutError:
            after = util.time.usec()
            await ctx.respond(
                f"""<b>Input</b>:
<pre language="bash">{escape(snip)}</pre>
<b>Output</b>:
🕑 Snippet failed to finish within 2 minutes."""
                f"Time: {util.time.format_duration_us(after - before)}",
                parse_mode=ParseMode.HTML,
            )
            return

        after = util.time.usec()

        el_us = after - before
        el_str = f"\nTime: {util.time.format_duration_us(el_us)}"

        if not stdout:
            stdout = "[no output]"
        elif stdout[-1:] != "\n":
            stdout += "\n"

        stdout = self.bot.redact_message(stdout)
        err = f"⚠️ Return code: {ret}" if ret != 0 else ""
        await ctx.respond(
            f"""<b>Input</b>:
<pre language="bash">{escape(snip)}</pre>
<b>Output</b>:
<pre language="bash">{escape(stdout)}</pre>{err}{el_str}""",
            parse_mode=ParseMode.HTML,
        )

    @command.desc("Update this bot from Git and restart")
    @command.usage("[remote name?]", optional=True)
    @command.alias("up", "upd")
    async def cmd_update(self, ctx: command.Context) -> Optional[str]:
        remote_name = ctx.input

        if not util.git.have_git:
            return "__The__ `git` __command is required for self-updating.__"

        # Attempt to get the Git repo
        repo = await util.run_sync(util.git.get_repo)
        if not repo:
            return "__Unable to locate Git repository data.__"

        if remote_name:
            # Attempt to get requested remote
            try:
                remote = await util.run_sync(repo.remote, remote_name)
            except ValueError:
                return f"__Remote__ `{remote_name}` __not found.__"
        else:
            # Get current branch's tracking remote
            remote = await util.run_sync(util.git.get_current_remote)
            if remote is None:
                return f"__Current branch__ `{repo.active_branch.name}` __is not tracking a remote.__"

        # Save time and old commit for diffing
        update_time = util.time.usec()
        old_commit = await util.run_sync(repo.commit)

        # Pull from remote
        await ctx.respond(f"Pulling changes from `{remote}`...")
        await util.run_sync(remote.pull)

        # Return early if no changes were pulled
        diff = old_commit.diff()
        if not diff:
            return "No updates found."

        # Check for dependency changes
        if any(change.a_path == "poetry.lock" for change in diff):
            # Update dependencies automatically if running in venv
            prefix = util.system.get_venv_path()
            if prefix:
                pip = str(AsyncPath(prefix) / "bin" / "pip")

                await ctx.respond("Updating dependencies...")
                stdout, _, ret = await util.system.run_command(
                    pip, "install", "-r", "requirements.txt"
                )
                if ret != 0:
                    return f"""⚠️ Error updating dependencies:
```{stdout}```
Fix the issue manually and then restart the bot."""
            else:
                return """Successfully pulled updates.
**Update dependencies manually** to avoid errors, then restart the bot for the update to take effect.
Dependency updates are automatic if you're running the bot in a virtualenv."""

        # Restart after updating
        return await self.cmd_restart(ctx, restart_time=update_time, reason="update")

    @command.desc("Get information about this bot instance")
    @command.alias("botinfo")
    async def cmd_info(self, ctx: command.Context) -> None:
        commit = await util.run_sync(util.version.get_commit)
        dirty = ", dirty" if await util.run_sync(util.git.is_dirty) else ""
        unofficial = (
            ", unofficial" if not await util.run_sync(util.git.is_official) else ""
        )
        version = (
            f"{__version__} (<code>{commit}</code>{dirty}{unofficial})"
            if commit
            else __version__
        )

        sys_ver = platform.release()
        try:
            sys_ver = sys_ver[: sys_ver.index("-")]
        except ValueError:
            pass

        now = util.time.usec()
        uptime = util.time.format_duration_us(now - self.bot.start_time_us)

        stats_module = self.bot.modules.get("Stats", None)
        get_start_time = getattr(stats_module, "get_start_time", None)
        total_uptime = None
        if stats_module and callable(get_start_time):
            stats_start_time = await get_start_time()
            total_uptime = util.time.format_duration_us(now - stats_start_time) + "\n"
        else:
            uptime += "\n"

        num_chats = await self.bot.client.get_dialogs_count()

        response = util.text.join_map(
            {
                "Version": version,
                "Python": f"{platform.python_implementation()} {platform.python_version()}",
                "System": f"{platform.system()} {sys_ver}",
                "Uptime": uptime,
                **({"Total uptime": total_uptime} if total_uptime else {}),
                "Commands loaded": len(self.bot.commands),
                "Modules loaded": len(self.bot.modules),
                "Listeners loaded": sum(
                    len(evt) for evt in self.bot.listeners.values()
                ),
                "Events activated": f"{self.bot.events_activated}\n",
                "Chats": num_chats,
            },
            heading='<a href="https://github.com/troublescope/re-caligo">RE-Caligo</a> info',
            parse_mode="html",
        )

        await ctx.respond(response, parse_mode=enums.ParseMode.HTML)
