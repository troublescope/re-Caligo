import asyncio
import re
import uuid
from collections import defaultdict
from typing import ClassVar, List, MutableMapping

from pymongo.asynchronous.collection import AsyncCollection
from pyrogram import enums, errors, filters, types
from pyrogram.utils import get_channel_id, unpack_inline_message_id

from caligo import command, listener, module, util


class Main(module.Module):
    name: ClassVar[str] = "Main"
    db: AsyncCollection

    async def on_load(self) -> None:
        self.db = self.bot.db[self.name.upper()]
        self.repo = self.bot.config["bot"]["git_url"]

        self._module_command_map: dict[str, dict[str, str]] = defaultdict(dict)
        for _, cmd in self.bot.commands.items():
            mod_name = cmd.module.name
            desc = cmd.desc or "<i>No description provided.</i>"
            aliases = f' (aliases: {", ".join(cmd.aliases)})' if cmd.aliases else ""
            self._module_command_map[mod_name][cmd.name] = desc + aliases

        # Store all modules for pagination
        self._all_modules = sorted(self._module_command_map.keys())
        self._modules_per_page = 8  # 4 rows × 2 buttons per row
        self._total_pages = (
            len(self._all_modules) + self._modules_per_page - 1
        ) // self._modules_per_page

    def build_button(self, page: int = 0) -> List[List[types.InlineKeyboardButton]]:
        """Build paginated buttons with 2 buttons per row"""
        buttons = []

        # Calculate start and end indices for current page
        start_idx = page * self._modules_per_page
        end_idx = min(start_idx + self._modules_per_page, len(self._all_modules))
        current_modules = self._all_modules[start_idx:end_idx]

        # Create module buttons (2 per row)
        for i in range(0, len(current_modules), 2):
            row = []
            for j in range(2):
                if i + j < len(current_modules):
                    mod = current_modules[i + j]
                    row.append(
                        types.InlineKeyboardButton(mod, callback_data=f"menu({mod})")
                    )
            buttons.append(row)

        # Add navigation buttons if we have more than one page
        if self._total_pages > 1:
            nav_row = []

            # Previous button
            prev_page = page - 1 if page > 0 else self._total_pages - 1
            nav_row.append(
                types.InlineKeyboardButton("«", callback_data=f"menu_page({prev_page})")
            )

            # Page indicator
            nav_row.append(
                types.InlineKeyboardButton(
                    f"{page + 1}/{self._total_pages}", callback_data="noop"
                )
            )

            # Next button
            next_page = page + 1 if page < self._total_pages - 1 else 0
            nav_row.append(
                types.InlineKeyboardButton("»", callback_data=f"menu_page({next_page})")
            )

            buttons.append(nav_row)

        # Add close button at bottom
        buttons.append(
            [types.InlineKeyboardButton("✗ Close", callback_data="menu(Close)")]
        )

        return buttons

    async def extract_inline_id(self, inline_id: str) -> tuple[int, int]:
        unpacked = await util.run_sync(unpack_inline_message_id, inline_id)
        return (
            (
                unpacked.owner_id
                if unpacked.owner_id == self.bot.uid
                else await util.run_sync(get_channel_id, abs(unpacked.owner_id))
            ),
            unpacked.id,
        )

    async def on_inline_query(self, query: types.InlineQuery) -> None:
        if query.query:
            return

        results = [
            types.InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title="About Caligo",
                input_message_content=types.InputTextMessageContent(
                    "<i>Caligo Simple. Powerful. Yours...</i>"
                ),
                description="Caligo Simple. Powerful. Yours..",
                reply_markup=types.InlineKeyboardMarkup(
                    [
                        [
                            types.InlineKeyboardButton(
                                "⚡️ Owner", user_id=self.bot.uid
                            ),
                            types.InlineKeyboardButton(
                                "📖️ Discussion", url="t.me/deltaDiscuss"
                            ),
                        ]
                    ]
                ),
            )
        ]

        if query.from_user and query.from_user.id == self.bot.uid:
            # Build first page buttons
            menu_buttons = await util.run_sync(self.build_button, 0)
            results.append(
                types.InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title="Menu",
                    input_message_content=types.InputTextMessageContent(
                        "<b>Re-Caligo Menu Helper</b>"
                    ),
                    description="Menu Helper.",
                    reply_markup=types.InlineKeyboardMarkup(menu_buttons),
                )
            )

        await query.answer(results=results, cache_time=5)

    @listener.priority(90)
    @listener.filters(filters.regex(r"menu(?:_page)?\((.+)\)$"))
    async def on_callback_query(self, query: types.CallbackQuery) -> None:
        if query.from_user and query.from_user.id != self.bot.uid:
            await query.answer("Not For You!", show_alert=True)
            return

        match = re.match(r"menu(?:_page)?\((.+)\)$", query.data)
        if not match:
            return

        action_or_page = match.group(1)

        if query.data.startswith("menu_page("):
            try:
                page = int(action_or_page)
                menu_buttons = await util.run_sync(self.build_button, page)
                await query.edit_message_text(
                    "<b>Caligo Menu Helper</b>",
                    reply_markup=types.InlineKeyboardMarkup(menu_buttons),
                )
            except (ValueError, errors.MessageNotModified):
                pass
            except errors.FloodWait as e:
                await asyncio.sleep(e.value)
            return

        if action_or_page == "noop":
            await query.answer()
            return

        mod = action_or_page

        if mod == "Back":
            try:
                menu_buttons = await util.run_sync(self.build_button, 0)
                await query.edit_message_text(
                    "<b>Caligo Menu Helper</b>",
                    reply_markup=types.InlineKeyboardMarkup(menu_buttons),
                )
            except errors.MessageNotModified:
                pass
            except errors.FloodWait as e:
                await asyncio.sleep(e.value)
            return

        if mod == "Close":
            try:
                chat_id, msg_id = await self.extract_inline_id(query.inline_message_id)
                await self.bot.client.delete_messages(chat_id, msg_id)
            except errors.ChatIdInvalid:
                await query.answer("😿️ Couldn't close message")
                menu_buttons = await util.run_sync(self.build_button, 0)
                # Remove close button for this case
                menu_buttons = menu_buttons[:-1]
                await query.edit_message_text(
                    "<b>Caligo Menu Helper</b>",
                    reply_markup=types.InlineKeyboardMarkup(menu_buttons),
                )
            return

        commands = self._module_command_map.get(mod)
        if not commands:
            await query.answer(f"😿️ {mod} doesn't have any commands.")
            return

        response = util.text.join_map(commands, heading=mod, parse_mode="html")

        back_button = [
            [types.InlineKeyboardButton("⇠ Back", callback_data="menu(Back)")]
        ]
        try:
            await query.edit_message_text(
                f"<blockquote expandable>{response}</blockquote>",
                reply_markup=types.InlineKeyboardMarkup(back_button),
            )
        except errors.MessageNotModified:
            pass
        except errors.FloodWait as e:
            await asyncio.sleep(e.value)

    @command.desc("List the commands")
    @command.usage("[filter: command or module name?]", optional=True)
    async def cmd_help(self, ctx: command.Context) -> str:
        await ctx.respond("<i>Processing...</i>")
        filt = ctx.input
        modules: MutableMapping[str, MutableMapping[str, str]] = defaultdict(dict)

        if self.bot.helper_initialized and not filt:
            response: Any
            try:
                response = await self.bot.client.get_inline_bot_results(
                    bot=self.bot.client_helper.me.username
                )
            except errors.BotInlineDisabled:
                return "<i>Bot Inline Disabled</i>"
            else:
                await ctx.msg.delete()

            if ctx.chat.is_forum:
                await self.bot.client.send_inline_bot_result(
                    ctx.msg.chat.id,
                    response.query_id,
                    response.results[1].id,
                    message_thread_id=ctx.msg.message_thread_id,
                )
            else:
                try:
                    await self.bot.client.send_inline_bot_result(
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
                    else None
                )

                args_desc = None
                if cmd.usage:
                    args_desc = cmd.usage
                    if cmd.usage_optional:
                        args_desc += " (optional)"
                    if cmd.usage_reply:
                        args_desc += " (also accepts replies)"

                data = {
                    "Command": f"<code>{cmd.name}</code>",
                    "Description": cmd.desc or "<i>No description provided.</i>",
                    "Module": cmd.module.name,
                }
                if aliases:
                    data["Aliases"] = aliases
                if args_desc:
                    data["Expected parameters"] = args_desc

                response = util.text.join_map(data, parse_mode="html")

                await ctx.respond(
                    text=(
                        f"<b>Help for <bold>{cmd.name}</bold></b>"
                        f"<blockquote expandable>\n{response}\n</blockquote>"
                    ),
                    parse_mode=enums.ParseMode.HTML,
                )
                return

            return "<i>That filter didn't match any commands or modules.</i>"

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

        full_response = "\n\n".join(response_sections)
        await ctx.respond(
            text=f"<blockquote expandable>\n{full_response}\n</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

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

    @command.desc("Get or change log chat ID")
    @command.alias("setlogchat", "getlogchat")
    @command.usage("[chat_id or 'here']", optional=True)
    async def cmd_logchat(self, ctx: command.Context) -> str:
        value = ctx.input.strip().lower() or ctx.flags.get("log_chat")

        if not value:
            return f"<b>Log Chat:</b> <code>{self.bot.log_chat}</code>"

        if value in {"here", "set"}:
            if ctx.chat.type == "private":
                return "<b>Error:</b> You can only set log chat to a group or channel."
            log_chat_id = ctx.chat.id
        else:
            try:
                log_chat_id = int(value)
            except ValueError:
                return "<b>Error:</b> log_chat must be a valid integer or use 'here'."

        self.bot.log_chat = log_chat_id
        await self.db.update_one(
            {"_id": 0}, {"$set": {"log_chat": log_chat_id}}, upsert=True
        )
        return f"<b>Log Chat set to:</b> <code>{self.bot.log_chat}</code>"
