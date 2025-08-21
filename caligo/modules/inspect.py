import asyncio
import inspect
import platform
import re
import sys
from typing import ClassVar, Optional

import aiohttp
import psutil
import pymongo
import pyrogram
from pyrogram.enums import ParseMode

from caligo import command, module, util


class Inspection(module.Module):
    name: ClassVar[str] = "Inspection"

    @command.desc("Get the code of a command")
    @command.usage("[command name]")
    async def cmd_src(self, ctx: command.Context) -> Optional[str]:
        cmd_name = ctx.input

        if cmd_name not in self.bot.commands:
            return f"__Command__ `{cmd_name}` __doesn't exist.__"

        src = await util.run_sync(inspect.getsource, self.bot.commands[cmd_name].func)
        filtered_src = re.sub(r"^ {4}", "", src, flags=re.MULTILINE)

        await ctx.respond(
            f"<pre language='python'>{filtered_src}</pre>", parse_mode=ParseMode.HTML
        )

    @command.desc("Get all contextually relevant IDs")
    @command.alias("user")
    async def cmd_id(self, ctx: command.Context) -> None:
        lines = []

        if ctx.msg.chat.id:
            lines.append(f"Chat ID: `{ctx.msg.chat.id}`")

        if ctx.msg.chat.is_forum:
            lines.append(f"Chat topic ID: `{ctx.msg.message_thread_id}`")

        lines.append(f"My user ID: `{self.bot.uid}`")

        if ctx.msg.reply_to_message:
            reply_msg = ctx.msg.reply_to_message
            sender = reply_msg.from_user
            lines.append(f"Message ID: `{reply_msg.id}`")

            if sender:
                lines.append(f"Message author ID: `{sender.id}`")

            if reply_msg.forward_from:
                lines.append(
                    f"Forwarded message author ID: `{reply_msg.forward_from.id}`"
                )

            f_chat = None
            if reply_msg.forward_from_chat:
                f_chat = reply_msg.forward_from_chat
                lines.append(f"Forwarded message {f_chat.type} ID: `{f_chat.id}`")

            f_msg_id = None
            if reply_msg.forward_from_message_id:
                f_msg_id = reply_msg.forward_from_message_id
                lines.append(f"Forwarded message original ID: `{f_msg_id}`")

            if f_chat is not None and f_msg_id is not None:
                uname = f_chat.username
                if uname is not None:
                    lines.append(
                        "[Link to forwarded message]"
                        f"(https://t.me/{uname}/{f_msg_id})"
                    )
                else:
                    lines.append(
                        "[Link to forwarded message]"
                        f"(https://t.me/{f_chat.id}/{f_msg_id})"
                    )

        text = (
            util.tg.pretty_print_entity(lines)
            .replace("'", "")
            .replace("list", "**List**")
        )
        await ctx.respond(text)

    @command.desc("Show CPU, memory, disk, and network information")
    async def cmd_sysinfo(self, ctx: command.Context) -> None:
        await ctx.respond("<b>Gathering system info...</b>")
        info = await asyncio.to_thread(self.get_info_text)
        await ctx.respond(info)

    def get_info_text(self) -> str:
        hrb = util.misc.human_readable_bytes
        join_map = util.text.join_map
        uname = platform.uname()
        sections = []

        # SYSTEM INFO
        sections.append(
            join_map(
                {
                    "System": uname.system,
                    "Node Name": uname.node,
                    "Release": uname.release,
                    "Version": uname.version,
                    "Machine": uname.machine,
                    "Processor": uname.processor or "N/A",
                    "Python": sys.version.split()[0],
                },
                heading="SYSTEM INFO",
                parse_mode="html",
            )
        )

        # LIBRARIES
        sections.append(
            join_map(
                {
                    "pyrogram": f"{pyrogram.__version__} "
                    f"(<b>{getattr(pyrogram, '__fork_name__', 'Official')}</b> | "
                    f"<code>{pyrogram.raw.all.layer}</code>)",
                    "aiohttp": aiohttp.__version__,
                    "pymongo": pymongo.__version__,
                },
                heading="LIBRARIES",
                parse_mode="html",
            )
        )

        # CPU INFO (manual formatting)
        cpu_lines = []
        try:
            cpu_lines.append(
                f"  • Physical cores: {psutil.cpu_count(logical=False) or 'N/A'}"
            )
        except Exception:
            cpu_lines.append("  • Physical cores: N/A")

        try:
            cpu_lines.append(
                f"  • Total cores: {psutil.cpu_count(logical=True) or 'N/A'}"
            )
        except Exception:
            cpu_lines.append("  • Total cores: N/A")

        try:
            freq = psutil.cpu_freq()
            cpu_lines.append(
                f"  • Frequency: {freq.current:.2f} MHz"
                if freq
                else "  • Frequency: N/A"
            )
        except Exception:
            cpu_lines.append("  • Frequency: N/A")

        try:
            usage_per_core = psutil.cpu_percent(interval=0.5, percpu=True)
            cpu_lines.append("  • Usage per core:")
            for i, u in enumerate(usage_per_core):
                cpu_lines.append(f"    • Core {i+1}: {u:.1f}%")
        except Exception:
            cpu_lines.append("  • Usage per core: N/A")

        try:
            total_usage = psutil.cpu_percent(interval=None)
            cpu_lines.append(f"  • Total usage: {total_usage:.1f}%")
        except Exception:
            cpu_lines.append("  • Total usage: N/A")

        sections.append("CPU INFO:\n" + "\n".join(cpu_lines))

        # MEMORY
        try:
            mem = psutil.virtual_memory()
            mem_info = {
                "Total": hrb(mem.total),
                "Available": hrb(mem.available),
                "Used": hrb(mem.used),
                "Percentage": f"{mem.percent}%",
            }
        except Exception:
            mem_info = {"Memory Info": "N/A"}

        sections.append(join_map(mem_info, heading="MEMORY", parse_mode="html"))

        # DISK INFO (skip useless mounts)
        try:
            partitions = psutil.disk_partitions()
        except Exception:
            partitions = []

        useless_mounts = {"/etc/resolv.conf", "/etc/hostname", "/etc/hosts"}

        if not partitions:
            sections.append(
                join_map({"Disk Info": "N/A"}, heading="DISK", parse_mode="html")
            )
        else:
            for part in partitions:
                if part.mountpoint in useless_mounts:
                    continue
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    sections.append(
                        join_map(
                            {
                                "Mountpoint": part.mountpoint,
                                "Type": part.fstype,
                                "Total": hrb(usage.total),
                                "Used": hrb(usage.used),
                                "Free": hrb(usage.free),
                                "Percent": f"{usage.percent}%",
                            },
                            heading=part.device,
                            parse_mode="html",
                        )
                    )
                except Exception:
                    continue

        # NETWORK
        try:
            net = psutil.net_io_counters()
            net_info = {"Sent": hrb(net.bytes_sent), "Recv": hrb(net.bytes_recv)}
        except Exception:
            net_info = {"Network Info": "N/A"}

        sections.append(join_map(net_info, heading="NETWORK", parse_mode="html"))

        return "\n\n".join(sections)
