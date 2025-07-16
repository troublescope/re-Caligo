import platform
import uuid
from collections import defaultdict
from typing import ClassVar, Dict, List, MutableMapping

from pyrogram import errors, filters, types
from pyrogram.utils import get_channel_id, unpack_inline_message_id

from caligo import __version__, command, listener, module, util
from caligo.core import database


class Main(module.Module):
    name: ClassVar[str] = "Main"
    cache: Dict[int, int]
    db: database.AsyncCollection

    async def on_load(self) -> None:
        self.db = self.bot.db[self.name.upper()]
        self.cache = {}
        self.repo = self.bot.config["bot"]["git_url"]

    async def extract_inline_id(self, inline_id: str) -> tuple[int, int]:
        unpacked = await util.run_sync(unpack_inline_message_id, inline_id)
        return (
            None
            if unpacked.owner_id == self.bot.uid
            else await util.run_sync(get_channel_id, abs(unpacked.owner_id))
        ), unpacked.id

    def build_button(self) -> List[List[types.InlineKeyboardButton]]:
        modules = list(self.bot.modules.keys())
        button: List[types.InlineKeyboardButton] = []
        for mod in modules:
            button.append(
                types.InlineKeyboardButton(mod, callback_data=f"menu({mod})".encode())
            )
        buttons = [
            button[i * 3 : (i + 1) * 3] for i in range((len(button) + 3 - 1) // 3)
        ]
        buttons.append(
            [
                types.InlineKeyboardButton(
                    "✗ Close", callback_data="menu(Close)".encode()
                )
            ]
        )

        return buttons

    async def on_inline_query(self, query: types.InlineQuery) -> None:
        answer = [
            types.InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title="About Caligo",
                input_message_content=types.InputTextMessageContent(
                    "<i>Caligo Simple. Powerful. Yours...</i>"
                ),
                description="Caligo Simple. Powerful. Yours..",
                thumb_url=None,
                reply_markup=types.InlineKeyboardMarkup(
                    [
                        [
                            types.InlineKeyboardButton(
                                "⚡️ Owner", user_id=self.bot.uid
                            ),
                            types.InlineKeyboardButton(
                                "📖️ Discussion ",
                                url="t.me/deltaDiscuss",
                            ),
                        ]
                    ]
                ),
            )
        ]
        if query.from_user and (query.from_user.id == self.bot.uid):
            button = await util.run_sync(self.build_button)
            answer.append(
                types.InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title="Menu",
                    input_message_content=types.InputTextMessageContent(
                        "<b>Caligo Menu Helper</b>"
                    ),
                    description="Menu Helper.",
                    thumb_url=None,
                    reply_markup=types.InlineKeyboardMarkup(button),
                )
            )

            return await query.answer(results=answer, cache_time=3)

    @listener.filters(filters.regex(r"menu\((\w+)\)$"))
    async def on_callback_query(self, query: types.CallbackQuery) -> None:
        if query.from_user and query.from_user.id != self.bot.uid:
            await query.answer("Not For You!", show_alert=True)
            return

        mod = query.matches[0].group(1)
        if mod == "Back":
            button = await util.run_sync(self.build_button)
            try:
                await query.edit_message_text(
                    "<b>Caligo Menu Helper</b>",
                    reply_markup=types.InlineKeyboardMarkup(button),
                )
            except FloodWait as e:
                await asyncio.sleep(e.x)
            return

        if mod == "Close":
            button = await util.run_sync(self.build_button)
            chat_id, msg_id = await self.extract_inline_id(query.inline_message_id)
            try:
                await self.bot.client.delete_messages(chat_id, msg_id)
            except errors.ChatIdInvalid:
                await query.answer("😿️ Couldn't close message")
                await query.edit_message_text(
                    "<b>Caligo Menu Helper</b>",
                    reply_markup=InlineKeyboardMarkup(button[:-1]),
                )

            return

        modules: MutableMapping[str, MutableMapping[str, str]] = defaultdict(dict)
        for _, cmd in self.bot.commands.items():
            if cmd.module.name != mod:
                continue

            desc = cmd.desc if cmd.desc else "<i>No description provided.</i>"
            aliases = ""
            if cmd.aliases:
                aliases = f' (aliases: {", ".join(cmd.aliases)})'

            mod_name = type(cmd.module).name
            modules[mod_name][cmd.name] = desc + aliases

        response = None
        for mod_name, commands in sorted(modules.items()):
            response = util.text.join_map(commands, heading=mod_name)

        if response is not None:
            button = [
                [
                    types.InlineKeyboardButton(
                        "⇠ Back", callback_data="menu(Back)".encode()
                    )
                ]
            ]
            await query.edit_message_text(
                response, reply_markup=types.InlineKeyboardMarkup(button)
            )

            return

        return await query.answer(f"😿️ {mod} doesn't have any commands.")

    @command.desc("List the commands")
    @command.usage("[filter: command or module name?]", optional=True)
    async def cmd_help(self, ctx: command.Context) -> str:
        filt = ctx.input
        modules: MutableMapping[str, MutableMapping[str, str]] = defaultdict(dict)
        if self.bot.helper_initialized and not filt:
            response: Any
            try:
                response = await self.bot.client.get_inline_bot_results(
                    self.bot.client_helper.me.username
                )
            except errors.BotInlineDisabled:
                return "<i>Bot Inline Disabled</i>"
            else:
                await ctx.msg.delete()

            if ctx.chat.is_forum:
                res: Any = await self.bot.client.send_inline_bot_result(
                    ctx.msg.chat.id,
                    response.query_id,
                    response.results[1].id,
                    message_thread_id=ctx.msg.message_thread_id,
                )
            else:
                try:
                    res: Any = await self.bot.client.send_inline_bot_result(
                        ctx.msg.chat.id, response.query_id, response.results[1].id
                    )

                except errors.FloodWait as e:
                    await asyncio.sleep(e.value)
            return

        # Handle command filters
        if filt and filt not in self.bot.modules:
            if filt in self.bot.commands:
                cmd = self.bot.commands[filt]
                aliases = (
                    f"<code>{'</code>, <code>'.join(cmd.aliases)}</code>"
                    if cmd.aliases
                    else "none"
                )
                args_desc = "none"
                if cmd.usage:
                    args_desc = cmd.usage
                    if cmd.usage_optional:
                        args_desc += " (optional)"
                    if cmd.usage_reply:
                        args_desc += " (also accepts replies)"

                return util.text.join_map(
                    {
                        "Command": f"<code>{cmd.name}</code>",
                        "Description": cmd.desc or "<i>No description provided.</i>",
                        "Module": cmd.module.name,
                        "Aliases": aliases,
                        "Expected parameters": args_desc,
                    },
                    parse_mode="html",
                )

            return "<i>That filter didn't match any commands or modules.</i>"

        # Gather full help
        for name, cmd in self.bot.commands.items():
            if filt:
                if cmd.module.name != filt:
                    continue
            else:
                if name != cmd.name:
                    continue

            desc = cmd.desc or "<i>No description provided</i>"
            aliases = f' (aliases: {", ".join(cmd.aliases)})' if cmd.aliases else ""
            mod_name = type(cmd.module).name
            modules[mod_name][cmd.name] = desc + aliases

        response_sections = []
        for mod_name, commands in sorted(modules.items()):
            section = util.text.join_map(commands, heading=mod_name, parse_mode="html")
            response_sections.append(section)

        # Final full expandable blockquote
        full_response = "\n\n".join(response_sections)
        return f"<blockquote expandable>\n{full_response}\n</blockquote>"

    @command.desc("Get or change this bot prefix")
    @command.alias("setprefix", "getprefix")
    @command.usage("[new prefix?]", optional=True)
    async def cmd_prefix(self, ctx: command.Context) -> str:
        new_prefix = ctx.input
        if not new_prefix:
            return f"<b>Prefix:</b> <code>{self.bot.prefix}</code>"

        self.bot.prefix = new_prefix
        await self.db.update_one(
            {"_id": 0}, {"$set": {"prefix": new_prefix}}, upsert=True
        )
        return f"<b>Prefix set to:</b> <code>{self.bot.prefix}</code>"

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

        await ctx.respond(response, parse_mode=ParseMode.HTML)
