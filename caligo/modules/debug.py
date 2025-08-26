import ast
import asyncio
import contextlib
import html
import inspect
import io
import os
import re
import sys
import traceback
from typing import Any, ClassVar, Dict, Optional, Tuple

import pyrogram
from aiopath import AsyncPath
from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

import caligo
from caligo import command, listener, module, util


class Debug(module.Module):
    name: ClassVar[str] = "Debug"

    tasks: Dict[Tuple[int, int], asyncio.Task[Any]]
    scopes: Dict[str, Any]

    async def on_load(self):
        self.tasks = dict()
        self.scopes = {
            "asyncio": asyncio,
            "contextlib": contextlib,
            "inspect": inspect,
            "io": io,
            "os": os,
            "re": re,
            "sys": sys,
            "traceback": traceback,
            #
            "caligo": caligo,
            "pyrogram": pyrogram,
            "raw": pyrogram.raw,
            "enums": pyrogram.enums,
            "types": pyrogram.types,
            #
            "self": self,
            "bot": self.bot,
        }

    @command.desc("Evaluate code")
    @command.usage("[code snippet]")
    @command.alias("exec", "e")
    async def cmd_eval(self, ctx: command.Context) -> Optional[str]:
        code = ctx.input
        if not code:
            return "Give me code to evaluate"

        await ctx.respond(f"<code>{html.escape(code)}</code>\n\n<b>Running...</b>")

        self.scopes["ctx"] = ctx

        start_time = util.time.usec()

        out_buf = io.StringIO()
        with contextlib.redirect_stdout(out_buf):
            task = asyncio.create_task(self.exec_function(code))
            self.tasks[(ctx.chat.id, ctx.msg.id)] = task
            try:
                result = await task
                output = out_buf.getvalue().rstrip() or str(result)
            except (asyncio.CancelledError, Exception):
                exception = traceback.TracebackException(*sys.exc_info())
                fmt_traceback = (
                    "".join(
                        traceback.format_list(
                            [
                                i
                                for i in exception.stack
                                if "site-packages" in i.filename
                            ]
                        )
                    )
                    or "  -"
                )
                output = (
                    f"{exception.exc_type.__name__}:"
                    f"\n  {exception._str if exception._str.strip() else '-'}"
                    f"\n\nTraceback:\n{fmt_traceback}"
                )

        if code.endswith("return"):
            return

        elapsed = util.time.usec() - start_time
        el_str = util.time.format_duration_us(elapsed)

        respond_text = (
            f"<b>Input:</b>\n<code>{html.escape(code)}</code>"
            f"\n\n<b>Output:</b>\n<code>{html.escape(output)}</code>"
            f"\n\n<b>{el_str}</b>"
        )

        if len(respond_text) > 2048:
            if len(output) > 1024:
                async with self.bot.http.post(
                    "https://paste.rs", data=output.encode()
                ) as resp:
                    paste_url = await resp.text()
                respond_text = (
                    "<b>Input:</b>"
                    f"\n<code>{html.escape(code[:512]) + '...' if len(code) > 1024 else html.escape(code)}</code>"
                    "\n\n<b>Output:</b>"
                    f"\n<code>{html.escape(output[:512]) + '...' if len(output) > 1024 else html.escape(output)}</code>"
                    f"\n\n<b><a href={paste_url}>{el_str}</a></b>"
                )

        await ctx.respond(respond_text, parse_mode=pyrogram.enums.ParseMode.HTML)

    @command.desc("Cancel evaluation")
    @command.usage("Reply to running task")
    @command.alias("c")
    async def cmd_cancel(self, ctx: command.Context) -> None:
        if not ctx.reply_msg:
            await ctx.respond("<i>Reply to an active task!</i>")
            return

        tasks = self.tasks.copy()
        for (chat_id, msg_id), task in tasks.items():
            if ctx.chat.id == chat_id and ctx.reply_msg.id == msg_id:
                task.cancel()
                self.tasks.pop((chat_id, msg_id), None)
                await ctx.respond("Cancelled", delete_after=2.5)
                break
            else:
                await ctx.respond("Reply to an active task!", delete_after=2.5)

    @command.desc("Show bot logs")
    @command.usage("[--lines N | --full] [--paste | -p] [--clear]")
    async def cmd_logs(self, ctx: command.Context):
        log_path = AsyncPath("caligo/caligo.log")

        if not await log_path.exists():
            await ctx.respond("❌ Log file not found.")
            return

        # Handle --clear
        if ctx.flags.get("clear"):
            await log_path.write_text("", encoding="utf-8")
            await ctx.respond("✅ Logs cleared.")
            return

        content = await log_path.read_text(encoding="utf-8")

        if ctx.flags.get("full", False):
            lines = content.strip().splitlines()
        else:
            try:
                limit = int(ctx.flags.get("lines", 10))
            except ValueError:
                await ctx.respond("❌ Invalid number for --lines")
                return
            lines = content.strip().splitlines()[-limit:]

        log_text = "\n".join(lines)
        if not log_text.strip():
            await ctx.respond("⚠️ Log file is empty.")
            return

        # Force paste if >4000 chars
        if ctx.flags.get("paste") or ctx.flags.get("p") or len(log_text) > 4000:
            self._log_cache = (
                "\n".join(content.strip().splitlines())
                if ctx.flags.get("full", False) or len(log_text) > 4000
                else log_text
            )
            try:
                bot_results = await self.bot.client.get_inline_bot_results(
                    self.bot.client_helper.me.username, "logs:paste"
                )
                if bot_results.results:
                    await ctx.msg.delete()
                    await self.bot.client.send_inline_bot_result(
                        ctx.msg.chat.id, bot_results.query_id, bot_results.results[0].id
                    )
                    return
            except Exception:
                # fallback to file upload if paste fails
                await ctx.msg.reply_document(str(log_path), caption="📜 Log file")
                return

        await ctx.respond(f"<pre>{log_text}</pre>", parse_mode=ParseMode.HTML)

    @listener.filters(filters.regex(r"^logs:paste$"))
    async def on_inline_query(self, query: InlineQuery) -> None:
        log_text = self._log_cache
        if not log_text:
            await query.answer(
                results=[],
                switch_pm_text="No logs cached.",
                switch_pm_parameter="start",
                cache_time=0,
            )
            return

        async with self.bot.http.post(
            "https://paste.rs", data=log_text.encode()
        ) as resp:
            paste_url = (await resp.text()).strip()

        reply_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Open Paste", url=paste_url + ".txt")]]
        )
        results = [
            InlineQueryResultArticle(
                title="📄 View logs on paste.rs",
                description="Click to open full logs in paste.rs",
                input_message_content=InputTextMessageContent(
                    f"Logs uploaded",
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False,
                ),
                reply_markup=reply_markup,
            )
        ]

        await query.answer(results=results, cache_time=0)

    async def exec_function(self, code: str) -> Any:
        body = ast.parse(code, "exec").body
        if isinstance(body[-1], ast.Expr):
            body[-1] = ast.Return(value=body[-1].value)

        name = "executor"
        node = ast.Module(
            body=[
                ast.AsyncFunctionDef(
                    name=name,
                    args=ast.arguments(
                        posonlyargs=[],
                        args=[ast.arg(arg=key) for key in self.scopes],
                        vararg=None,
                        kwonlyargs=[],
                        kw_defaults=[],
                        kwarg=None,
                        defaults=[],
                    ),
                    body=body,
                    decorator_list=[],
                    returns=None,
                    type_comments=[],
                    type_params=[],
                )
            ],
            type_ignores=[],
        )
        ast.fix_missing_locations(node)

        scope = {}
        exec(compile(node, "<string>", "exec"), scope)

        coro = await scope[name](*self.scopes.values())
        return await coro if hasattr(coro, "__await__") else coro
