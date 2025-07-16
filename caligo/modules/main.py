import platform
from collections import defaultdict
from typing import ClassVar, MutableMapping

from pyrogram.enums import ParseMode

from caligo import __version__, command, module, util
from caligo.core import database


class Main(module.Module):
    name: ClassVar[str] = "Main"
    db: database.AsyncCollection

    async def on_load(self) -> None:
        self.db = self.bot.db[self.name.upper()]

    @command.desc("List the commands")
    @command.usage("[filter: command or module name?]", optional=True)
    async def cmd_help(self, ctx: command.Context) -> str:
        filt = ctx.input
        modules: MutableMapping[str, MutableMapping[str, str]] = defaultdict(dict)

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
