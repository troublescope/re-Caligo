import re
from datetime import datetime, timedelta, timezone
from typing import ClassVar, Optional

import pytz

from caligo import command, module

JAKARTA_TZ = pytz.timezone("Asia/Jakarta")


def human_delta(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "0 seconds"
    periods = [
        ("year", 60 * 60 * 24 * 365),
        ("week", 60 * 60 * 24 * 7),
        ("day", 60 * 60 * 24),
        ("hour", 60 * 60),
        ("minute", 60),
        ("second", 1),
    ]
    parts = []
    for name, count in periods:
        value, seconds = divmod(seconds, count)
        if value:
            parts.append(f"{value} {name}{'s' if value > 1 else ''}")
    return " ".join(parts)


class Reminders(module.Module):
    name: ClassVar[str] = "Reminders"

    def _parse_time(self, t: str) -> Optional[timedelta]:
        """Parse waktu input (WIB). Return timedelta dari sekarang."""
        now = datetime.now(JAKARTA_TZ)

        # HH:MM
        if re.match(r"^\d{2}:\d{2}$", t):
            hh, mm = map(int, t.split(":"))
            target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if target < now:
                target += timedelta(days=1)
            return target - now

        # YYYY-MM-DD_HH:MM
        elif re.match(r"^\d{4}-\d{2}-\d{2}_\d{2}:\d{2}$", t):
            dt = datetime.strptime(t, "%Y-%m-%d_%H:%M")
            dt = JAKARTA_TZ.localize(dt)
            return dt - now

        # relative (short & long)
        delta = timedelta()
        short_pattern = re.findall(r"(\d+)([smhdwy])", t)
        long_pattern = re.findall(
            r"(\d+)\s*(seconds?|minutes?|hours?|days?|weeks?|years?)", t, re.I
        )

        if not short_pattern and not long_pattern:
            return None

        for val, unit in short_pattern:
            val = int(val)
            if unit == "s":
                delta += timedelta(seconds=val)
            elif unit == "m":
                delta += timedelta(minutes=val)
            elif unit == "h":
                delta += timedelta(hours=val)
            elif unit == "d":
                delta += timedelta(days=val)
            elif unit == "w":
                delta += timedelta(weeks=val)
            elif unit == "y":
                delta += timedelta(days=365 * val)

        for val, unit in long_pattern:
            val = int(val)
            unit = unit.lower()
            if "second" in unit:
                delta += timedelta(seconds=val)
            elif "minute" in unit:
                delta += timedelta(minutes=val)
            elif "hour" in unit:
                delta += timedelta(hours=val)
            elif "day" in unit:
                delta += timedelta(days=val)
            elif "week" in unit:
                delta += timedelta(weeks=val)
            elif "year" in unit:
                delta += timedelta(days=365 * val)

        return delta

    async def _set_reminder(
        self, ctx: command.Context, silent: bool, personal: bool
    ) -> None:
        if "t" not in ctx.flags:
            await ctx.respond(
                "Usage: remind -t (time) [-x repeat] (text or reply to media)"
            )
            return

        time_arg = ctx.flags.get("t")
        delta = self._parse_time(time_arg)
        if not delta:
            await ctx.respond("Invalid time format.")
            return

        # ambil jumlah repeat
        count = ctx.flags.get("x")
        try:
            count = int(count.split()[0])
        except (ValueError, AttributeError):
            return "__Use integer for -x flags__"

        # ambil text dari sisa argumen, bukan dari flags
        args = ctx.input.split()
        clean_args = []
        skip_next = False
        for i, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            if arg in ("-t", "-x"):
                skip_next = True
                continue
            clean_args.append(arg)

        text = " ".join(clean_args).strip()

        now = datetime.now(JAKARTA_TZ)
        target_chat = ctx.msg.from_user.id if personal else ctx.msg.chat.id

        try:
            when_wib = now + delta
            for i in range(count):
                when_utc = when_wib.astimezone(timezone.utc)

                if text:
                    await self.bot.client.send_message(
                        target_chat, text, schedule_date=when_utc
                    )
                elif ctx.reply_msg:
                    await self.bot.client.copy_message(
                        chat_id=target_chat,
                        from_chat_id=ctx.msg.chat.id,
                        message_id=ctx.reply_msg.id,
                        schedule_date=when_utc,
                    )
                else:
                    await ctx.respond(
                        "You must provide reminder text or reply to a message."
                    )
                    return

                when_wib += delta

            if not silent:
                first_run = (now + delta).astimezone(JAKARTA_TZ)
                info = f"**🔔 Reminder aktif!**\n\n"
                info += f"📅 **Waktu pertama:** {first_run.strftime('%A, %d %B %Y %H:%M:%S %Z')}\n"
                info += f"⏳ **Countdown: **{human_delta(delta)}\n"
                if count > 1:
                    info += f"🔁 **Akan diulang** `{count}` **kali setiap** `{human_delta(delta)}`"
                try:
                    await ctx.respond(info)
                except Exception:
                    pass
            else:
                await ctx.msg.delete()

        except Exception as e:
            await ctx.respond(f"❌ Gagal menjadwalkan reminder: {e}")

    @command.desc("Set a reminder in the current chat.")
    @command.usage("remind -t (time) [-x repeat] (text or reply)")
    async def cmd_remind(self, ctx: command.Context) -> None:
        await self._set_reminder(ctx, silent=False, personal=False)

    @command.desc("Set a reminder for yourself (private message).")
    @command.usage("remindme -t (time) [-x repeat] (text or reply)")
    async def cmd_remindme(self, ctx: command.Context) -> None:
        await self._set_reminder(ctx, silent=False, personal=True)

    @command.desc("Set a silent reminder in the current chat.")
    @command.usage("sremind -t (time) [-x repeat] (text or reply)")
    async def cmd_sremind(self, ctx: command.Context) -> None:
        await self._set_reminder(ctx, silent=True, personal=False)

    @command.desc("Set a silent personal reminder.")
    @command.usage("sremindme -t (time> [-x repeat] (text or reply)")
    async def cmd_sremindme(self, ctx: command.Context) -> None:
        await self._set_reminder(ctx, silent=True, personal=True)
