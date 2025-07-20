import inspect
import re
from typing import ClassVar, Optional

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
        # Strip first level of indentation
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
        await ctx.respond(text, disable_web_page_preview=True)
