import asyncio
import random
from datetime import datetime

from pyrogram import filters
from pyrogram.types import Message

from caligo import command, listener, module, util


class AFK(module.Module):
    name = "AFK"

    async def on_load(self):
        self.db = self.bot.db.get_collection("AFK")
        self._is_afk = await self.get("is_afk") or False
        self._start_time = await self.get("start") or 0
        self._reason = await self.get("reason") or ""
        self._afk_cache = {}
        self._afk_links = []
        self._afk_limit = 3
        self._afk_cooldown = 60
        self._afk_delete_after = await self.get("afk_delete") or 60
        self._afk_ignore_chats = await self.get("ignore_chats") or []
        self._afk_delete_tasks = {}

    async def get(self, key: str):
        doc = await self.db.find_one({"_id": 0})
        return doc.get(key) if doc else None

    async def put(self, key: str, value):
        await self.db.update_one({"_id": 0}, {"$set": {key: value}}, upsert=True)

    async def _schedule_delete(self, msg: Message, delay: int):
        try:
            await asyncio.sleep(delay)
            await msg.delete(revoke=True)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    @listener.priority(99)
    @listener.filters(
        (filters.mentioned | filters.private) & ~filters.me & ~filters.bot
    )
    async def on_message(self, msg: Message):
        if not self._is_afk:
            return
        if msg.from_user and msg.from_user.id == self.bot.uid:
            return
        if msg.chat.id in self._afk_ignore_chats:
            return

        user_id = msg.from_user.id if msg.from_user else None
        now = datetime.now().timestamp()

        if user_id:
            user_data = self._afk_cache.get(user_id, {"count": 0, "last_reset": 0})
            if now - user_data["last_reset"] > self._afk_cooldown:
                user_data = {"count": 0, "last_reset": now}
            if user_data["count"] >= self._afk_limit:
                return
            user_data["count"] += 1
            self._afk_cache[user_id] = user_data

        if msg.link:
            self._afk_links.append(
                {
                    "link": msg.link,
                    "user": (
                        msg.from_user.username
                        if msg.from_user and msg.from_user.username
                        else None
                    ),
                    "name": msg.from_user.first_name if msg.from_user else "unknown",
                }
            )

        duration = util.time.format_duration_us(
            util.time.usec() - self._start_time * 1_000_000
        )

        reason = self._reason.strip()
        quote_block = ""

        if not reason:
            try:
                async with self.bot.http.get(
                    "https://quotes-api-self.vercel.app/quote"
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        q = data.get("quote", "").strip()
                        a = data.get("author", "").strip()
                        if q:
                            author = a or "unknown"
                            quote_block = f'<pre language="{author}">{q}</pre>'
            except Exception:
                pass

        afk_phrases = [
            "💤 I've been away for {duration}.",
            "🚪 I left {duration} ago.",
            "📴 Offline since {duration}.",
            "🏝 Been gone for {duration}.",
            "👻 Disappeared {duration} ago.",
            "🕳 Fell into a void {duration} ago.",
            "🙈 Not here since {duration}.",
            "😴 Vanished for {duration}.",
            "🪐 Floating in space for {duration}.",
            "🚶 Walked away {duration} ago.",
        ]

        text = random.choice(afk_phrases).format(duration=duration)

        if reason:
            text += f" — <i>{reason}</i>"
        elif quote_block:
            text += quote_block

        try:
            r = await msg.reply(text, quote=True)
            if self._afk_delete_after > 0:
                old_task = self._afk_delete_tasks.pop(r.id, None)
                if old_task:
                    old_task.cancel()

                task = asyncio.create_task(
                    self._schedule_delete(r, self._afk_delete_after)
                )
                self._afk_delete_tasks[r.id] = task
        except Exception:
            pass

    @command.desc("Toggle AFK mode with optional reason")
    @command.usage("[reason?]", optional=True)
    async def cmd_afk(self, ctx: command.Context):
        reason_input = ctx.input.strip()
        now = int(datetime.now().timestamp())

        if self._is_afk and not reason_input:
            duration = util.time.format_duration_us(
                util.time.usec() - self._start_time * 1_000_000
            )
            text = f"<b>You're no longer AFK.</b>\nYou were AFK for <code>{duration}</code>."

            if self._afk_links:
                recent = []
                for m in self._afk_links[-10:]:
                    link = m["link"]
                    label = (
                        f"@{m['user']}" if m.get("user") else m.get("name", "someone")
                    )
                    recent.append(f'• <a href="{link}">{label}</a>')
                text += f"\n\nMentions while AFK:\n" + "\n".join(recent)

            await self.put("is_afk", False)
            await self.put("start", 0)
            await self.put("reason", "")
            self._is_afk = False
            self._start_time = 0
            self._reason = ""
            self._afk_links.clear()
            return text

        if self._is_afk and reason_input:
            await self.put("reason", reason_input)
            self._reason = reason_input
            return f"<b>AFK reason updated:</b> <i>{reason_input}</i>"

        await self.put("is_afk", True)
        await self.put("start", now)
        await self.put("reason", reason_input)
        self._is_afk = True
        self._start_time = now
        self._reason = reason_input
        self._afk_links.clear()

        return f"<blockquote><b>You're now AFK.</b>{f' Reason: <i>{reason_input}</i>' if reason_input else ''}</blockquote>"

    @command.desc("Set auto-delete time for AFK replies (0 to disable)")
    @command.usage("[seconds]", optional=True)
    async def cmd_afkdel(self, ctx: command.Context):
        arg = ctx.input.strip()
        if not arg:
            return f"AFK reply delete time is set to <b>{self._afk_delete_after}</b> seconds."

        try:
            seconds = int(arg)
            if seconds < 0 or seconds > 3600:
                return "Please provide a time between 0 and 3600 seconds."
        except ValueError:
            return "Invalid number."

        self._afk_delete_after = seconds
        await self.put("afk_delete", seconds)

        if seconds == 0:
            return "AFK replies will no longer be auto-deleted."
        return f"AFK replies will auto-delete after <b>{seconds}</b> seconds."

    @command.desc("Manage AFK blacklist (ignore specific chats)")
    @command.usage("[chat_id|list|clear]", optional=True)
    async def cmd_afkbl(self, ctx: command.Context):
        arg = ctx.input.strip()

        if arg.lower() == "list":
            if not self._afk_ignore_chats:
                return "AFK ignore list is empty."
            lines = [f"• <code>{cid}</code>" for cid in self._afk_ignore_chats]
            return "<b>AFK ignore list:</b>\n" + "\n".join(lines)

        if arg.lower() == "clear":
            count = len(self._afk_ignore_chats)
            self._afk_ignore_chats.clear()
            await self.put("ignore_chats", [])
            return f"Cleared <b>{count}</b> chats from AFK ignore list."

        try:
            chat_id = int(arg) if arg else ctx.msg.chat.id
        except ValueError:
            return "Invalid chat ID."

        self._afk_ignore_chats = [int(c) for c in self._afk_ignore_chats]

        if chat_id in self._afk_ignore_chats:
            self._afk_ignore_chats.remove(chat_id)
            status = "removed from"
        else:
            self._afk_ignore_chats.append(chat_id)
            status = "added to"

        await self.put("ignore_chats", self._afk_ignore_chats)
        return f"<b>{chat_id}</b> {status} AFK ignore list."
