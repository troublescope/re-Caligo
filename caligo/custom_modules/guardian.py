from typing import Any, ClassVar, Dict, Set

from pyrogram import enums, filters
from pyrogram.types import Message

from caligo import command, listener, module, util
from caligo.core.database import AsyncCollection


class Guardian(module.Module):
    name: ClassVar[str] = "Guardian"

    db: AsyncCollection
    approved_users: Set[int]
    settings: Dict[str, Any]
    pending_users: Dict[int, int]

    async def on_load(self) -> None:
        self.db = self.bot.db.get_collection(self.name.upper())
        self.approved_users = set()
        self.pending_users = {}
        self.settings = {
            "enabled": False,
            "max_flood": 5,
            "auto_approve": True,
            "welcome_message": "<b>🛡️ Private Message Guardian</b>\n\nPlease wait for approval to send messages.",
            "approved_message": "✅ You have been approved to send messages.",
            "blocked_message": "❌ You have been blocked from sending messages.",
        }

    async def on_start(self, _: int) -> None:
        data = await self.db.find_one({"_id": 0})
        if data:
            self.settings.update(data.get("settings", {}))
            self.approved_users = set(data.get("approved_users", []))

    async def _save_data(self) -> None:
        await self.db.update_one(
            {"_id": 0},
            {
                "$set": {
                    "settings": self.settings,
                    "approved_users": list(self.approved_users),
                }
            },
            upsert=True,
        )

    @listener.filters(filters.private & ~filters.me & ~filters.bot)
    async def on_message(self, message: Message) -> None:
        if not self.settings["enabled"]:
            return

        user = message.from_user

        if user.id in self.approved_users:
            return

        self.pending_users.setdefault(user.id, 0)
        self.pending_users[user.id] += 1

        if self.pending_users[user.id] == 1:
            await message.reply_text(
                self.settings["welcome_message"],
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )

        if self.pending_users[user.id] > self.settings["max_flood"]:
            await message.reply_text(
                self.settings["blocked_message"],
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
            await user.block()
            self.pending_users.pop(user.id, None)
            return

        await message.delete()

    @command.desc("Toggle Private Message Guardian on/off")
    @command.usage("pmpermit [--on] [--off] [--status]", optional=True)
    async def cmd_pmpermit(self, ctx: command.Context) -> str:
        if ctx.flags.get("on") or ctx.flags.get("enable"):
            self.settings["enabled"] = True
        elif ctx.flags.get("off") or ctx.flags.get("disable"):
            self.settings["enabled"] = False
        elif ctx.flags.get("status") or ctx.flags.get("s"):
            status = (
                "🟢 <b>enabled</b>"
                if self.settings["enabled"]
                else "🔴 <b>disabled</b>"
            )
            return f"<b>Guardian is currently {status}.</b>"
        elif ctx.args:
            arg = ctx.args[0].lower()
            if arg in {"on", "enable", "true"}:
                self.settings["enabled"] = True
            elif arg in {"off", "disable", "false"}:
                self.settings["enabled"] = False
            else:
                return "❌ <b>Invalid argument.</b>\nUse <code>--on</code>, <code>--off</code>, or <code>--status</code>."
        else:
            self.settings["enabled"] = not self.settings["enabled"]

        await self._save_data()
        status = (
            "🟢 <b>enabled</b>" if self.settings["enabled"] else "🔴 <b>disabled</b>"
        )
        return f"<b>Guardian</b> {status}!"

    @command.desc("Approve a user to send private messages")
    @command.usage(
        "approve [--all-pending] [reply to message] [<user_id_or_username>]",
        optional=True,
    )
    async def cmd_approve(self, ctx: command.Context) -> str:
        if ctx.flags.get("all-pending") or ctx.flags.get("all"):
            if not self.pending_users:
                return "ℹ️ No pending users to approve."

            count = len(self.pending_users)
            for user_id in list(self.pending_users.keys()):
                self.approved_users.add(user_id)
                try:
                    await self.bot.client.send_message(
                        user_id,
                        self.settings["approved_message"],
                        parse_mode=enums.ParseMode.HTML,
                    )
                except Exception:
                    pass

            self.pending_users.clear()
            await self._save_data()
            return f"✅ Approved all <b>{count}</b> pending users."

        user_id = None
        if ctx.input:
            user_id = await util.tg.extract_user_id(ctx.msg, ctx.input)
        elif ctx.msg.reply_to_message:
            user_id = ctx.msg.reply_to_message.from_user.id
        else:
            user_id = ctx.msg.chat.id if "private" in ctx.msg.chat.type.value else None

        if not user_id:
            return "❌ Reply to a message or provide user ID/username as argument."

        if user_id in self.approved_users:
            return "ℹ️ User is already approved."

        self.approved_users.add(user_id)
        self.pending_users.pop(user_id, None)
        await self._save_data()

        if not ctx.flags.get("silent") and not ctx.flags.get("s"):
            try:
                await ctx.bot.send_message(
                    user_id,
                    self.settings["approved_message"],
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass

        return f"✅ User <code>{user_id}</code> has been approved."

    @command.desc("Disapprove a user from sending private messages")
    @command.usage(
        "disapprove [--block] [reply to message] [<user_id_or_username>]", optional=True
    )
    async def cmd_disapprove(self, ctx: command.Context) -> str:
        user_id = None

        # Check if replying to a message

        if ctx.input:
            user_id = await util.tg.extract_user_id(ctx.msg, ctx.input)
        elif ctx.msg.reply_to_message:
            user_id = ctx.msg.reply_to_message.from_user.id
        else:
            user_id = ctx.msg.chat.id if "private" in ctx.msg.chat.type.value else None

        if not user_id:
            return "❌ Reply to a message or provide user ID/username as argument."

        if user_id not in self.approved_users:
            return "ℹ️ User is not approved."

        self.approved_users.remove(user_id)

        if ctx.flags.get("block") or ctx.flags.get("b"):
            try:
                await ctx.bot.block_user(user_id)
            except Exception:
                pass

        await self._save_data()
        block_msg = (
            " and blocked" if ctx.flags.get("block") or ctx.flags.get("b") else ""
        )
        return f"✅ User <code>{user_id}</code> has been disapproved{block_msg}."

    @command.desc("List all approved users")
    @command.usage("approved [--count] [--ids-only]", optional=True)
    async def cmd_approved(self, ctx: command.Context) -> str:
        if not self.approved_users:
            return "ℹ️ No approved users found."

        if ctx.flags.get("count") or ctx.flags.get("c"):
            return f"📊 Total approved users: <b>{len(self.approved_users)}</b>"

        if ctx.flags.get("ids-only") or ctx.flags.get("ids"):
            ids = ", ".join(f"<code>{uid}</code>" for uid in self.approved_users)
            return f"<b>Approved User IDs:</b> {ids}"

        lines = []
        for uid in self.approved_users:
            try:
                user = await ctx.bot.get_users(uid)
                name = f"{user.first_name} {user.last_name or ''}".strip()
                username = f"@{user.username}" if user.username else ""
                lines.append(f"• <b>{name}</b> {username} (<code>{uid}</code>)")
            except Exception:
                lines.append(f"• Unknown User (<code>{uid}</code>)")

        # Show max 15 users to avoid flooding
        display_lines = lines[:15]
        result = (
            f"<b>✅ Approved Users ({len(self.approved_users)}):</b>\n"
            + "\n".join(display_lines)
        )

        if len(lines) > 15:
            result += f"\n... and <b>{len(lines) - 15}</b> more users"

        return result

    @command.desc("Configure Guardian settings")
    @command.usage(
        "pmset [--show] [-l <limit>] [-a <on/off>] [-w <message>] [--approved <message>] [--blocked <message>]",
        optional=True,
    )
    async def cmd_pmset(self, ctx: command.Context) -> str:
        if (
            ctx.flags.get("show")
            or ctx.flags.get("s")
            or (not ctx.flags and not ctx.args)
        ):
            settings_display = []
            for k, v in self.settings.items():
                if k.endswith("_message"):
                    # Show truncated message preview
                    preview = (v[:40] + "...") if len(str(v)) > 40 else v
                    settings_display.append(f"• <code>{k}</code>: {preview}")
                else:
                    settings_display.append(f"• <code>{k}</code>: <b>{v}</b>")

            return "<b>⚙️ Guardian Settings:</b>\n" + "\n".join(settings_display)

        changes_made = []

        if "l" in ctx.flags or "limit" in ctx.flags:
            try:
                limit = int(ctx.flags.get("l") or ctx.flags.get("limit"))
                if limit < 1:
                    return "❌ Flood limit must be at least 1."
                self.settings["max_flood"] = limit
                changes_made.append(f"<b>max_flood</b> = <code>{limit}</code>")
            except (ValueError, TypeError):
                return "❌ Invalid flood limit value."

        if "a" in ctx.flags or "auto" in ctx.flags:
            auto_val = str(ctx.flags.get("a") or ctx.flags.get("auto")).lower()
            if auto_val in {"true", "yes", "1", "on"}:
                self.settings["auto_approve"] = True
                changes_made.append("<b>auto_approve</b> = <code>True</code>")
            elif auto_val in {"false", "no", "0", "off"}:
                self.settings["auto_approve"] = False
                changes_made.append("<b>auto_approve</b> = <code>False</code>")
            else:
                return "❌ Invalid auto_approve value. Use on/off, true/false, yes/no, or 1/0."

        if "w" in ctx.flags or "welcome" in ctx.flags:
            welcome_msg = ctx.flags.get("w") or ctx.flags.get("welcome")
            if welcome_msg:
                self.settings["welcome_message"] = str(welcome_msg)
                changes_made.append("<b>welcome_message</b> updated")

        if "approved" in ctx.flags:
            approved_msg = ctx.flags.get("approved")
            if approved_msg:
                self.settings["approved_message"] = str(approved_msg)
                changes_made.append("<b>approved_message</b> updated")

        if "blocked" in ctx.flags:
            blocked_msg = ctx.flags.get("blocked")
            if blocked_msg:
                self.settings["blocked_message"] = str(blocked_msg)
                changes_made.append("<b>blocked_message</b> updated")

        # Fallback to positional arguments
        if not changes_made and ctx.args:
            setting_key = ctx.args[0]
            if len(ctx.args) > 1:
                setting_value = " ".join(ctx.args[1:])

                if setting_key not in self.settings:
                    return f"❌ Unknown setting '<code>{setting_key}</code>'."

                try:
                    current_type = type(self.settings[setting_key])
                    if current_type is bool:
                        setting_value = str(setting_value).lower() in {
                            "true",
                            "yes",
                            "1",
                            "on",
                        }
                    elif current_type is int:
                        setting_value = int(setting_value)
                except Exception:
                    return f"❌ Invalid type for setting '<code>{setting_key}</code>'."

                self.settings[setting_key] = setting_value
                changes_made.append(
                    f"<b>{setting_key}</b> = <code>{setting_value}</code>"
                )
            else:
                if setting_key in self.settings:
                    return f"<code>{setting_key}</code>: <b>{self.settings[setting_key]}</b>"
                else:
                    return f"❌ Unknown setting '<code>{setting_key}</code>'."

        if not changes_made:
            return "ℹ️ No valid settings provided. Use <code>--show</code> to see current settings."

        await self._save_data()
        return "<b>✅ Updated settings:</b>\n" + "\n".join(
            f"• {change}" for change in changes_made
        )

    @command.desc("Clear all pending users")
    @command.usage("pmclear [--count-only]", optional=True)
    async def cmd_pmclear(self, ctx: command.Context) -> str:
        count = len(self.pending_users)

        if ctx.flags.get("count-only") or ctx.flags.get("count"):
            return f"📊 Current pending users: <b>{count}</b>"

        self.pending_users.clear()
        return f"🧹 Cleared <b>{count}</b> pending users."

    @command.desc("Show Guardian status and statistics")
    @command.usage("pmstatus [--detailed]", optional=True)
    async def cmd_pmstatus(self, ctx: command.Context) -> str:
        status_icon = "🟢" if self.settings["enabled"] else "🔴"
        status = "enabled" if self.settings["enabled"] else "disabled"

        basic_info = (
            f"<b>🛡️ Guardian Status:</b> {status_icon} <b>{status}</b>\n"
            f"<b>✅ Approved users:</b> {len(self.approved_users)}\n"
            f"<b>⏳ Pending users:</b> {len(self.pending_users)}"
        )

        if not (ctx.flags.get("detailed") or ctx.flags.get("d")):
            return basic_info

        # Show key settings in detailed mode
        key_settings = [
            f"• <b>Max flood:</b> <code>{self.settings['max_flood']}</code>",
            f"• <b>Auto approve:</b> <code>{self.settings['auto_approve']}</code>",
        ]

        settings_info = "\n\n<b>⚙️ Settings:</b>\n" + "\n".join(key_settings)

        if self.pending_users:
            pending_info = f"\n\n<b>📊 Pending Users:</b>\n" + "\n".join(
                f"• <code>{uid}</code>: {count} messages"
                for uid, count in list(self.pending_users.items())[:10]
            )
            if len(self.pending_users) > 10:
                pending_info += f"\n... and <b>{len(self.pending_users) - 10}</b> more"
        else:
            pending_info = ""

        return basic_info + settings_info + pending_info
