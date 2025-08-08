import ast
import inspect
import io
import os
import re
import sys
import traceback
from contextlib import redirect_stdout
from html import escape
from typing import Any, ClassVar, Optional, Tuple

import aiopath
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

from caligo import command, listener, module, util


async def reval(
    code: str, globs: dict, **kwargs
) -> Tuple[Any, float, Optional[Exception]]:
    locs: dict = {}
    globs = globs.copy()

    global_args = "_globs"
    while global_args in globs or global_args in kwargs:
        global_args = "_" + global_args

    kwargs[global_args] = {
        k: globs.get(k) for k in ("__name__", "__package__") if k in globs
    }

    root = ast.parse(code, mode="exec")
    code_body = root.body

    if not code_body:
        return None, 0.0, None

    ret_name = "_ret"
    while ret_name in globs or any(
        isinstance(n, ast.Name) and n.id == ret_name for n in ast.walk(root)
    ):
        ret_name = "_" + ret_name

    ret_list = ast.Assign(
        targets=[ast.Name(id=ret_name, ctx=ast.Store())],
        value=ast.List(elts=[], ctx=ast.Load()),
    )
    ast.fix_missing_locations(ret_list)

    inject_globals = ast.Expr(
        ast.Call(
            func=ast.Attribute(
                value=ast.Call(
                    func=ast.Name("globals", ast.Load()), args=[], keywords=[]
                ),
                attr="update",
                ctx=ast.Load(),
            ),
            args=[],
            keywords=[
                ast.keyword(arg=None, value=ast.Name(id=global_args, ctx=ast.Load()))
            ],
        )
    )
    ast.fix_missing_locations(inject_globals)

    if not any(isinstance(n, ast.Return) for n in code_body):
        for i, stmt in enumerate(code_body):
            if isinstance(stmt, ast.Expr) and (
                i == len(code_body) - 1 or not isinstance(stmt.value, ast.Call)
            ):
                code_body[i] = ast.Expr(
                    value=ast.Call(
                        func=ast.Attribute(
                            value=ast.Name(id=ret_name, ctx=ast.Load()),
                            attr="append",
                            ctx=ast.Load(),
                        ),
                        args=[stmt.value],
                        keywords=[],
                    )
                )
                ast.fix_missing_locations(code_body[i])
    else:
        for stmt in code_body:
            if isinstance(stmt, ast.Return) and stmt.value:
                stmt.value = ast.List(elts=[stmt.value], ctx=ast.Load())
                ast.fix_missing_locations(stmt)

    code_body.append(ast.Return(value=ast.Name(id=ret_name, ctx=ast.Load())))
    ast.fix_missing_locations(code_body[-1])

    arguments = ast.arguments(
        posonlyargs=[],
        args=[],
        vararg=None,
        kwonlyargs=[ast.arg(arg=k) for k in kwargs],
        kw_defaults=[None] * len(kwargs),
        kwarg=None,
        defaults=[],
    )

    func_def = ast.AsyncFunctionDef(
        name="__reval_func__",
        args=arguments,
        body=[inject_globals, ret_list] + code_body,
        decorator_list=[],
    )
    ast.fix_missing_locations(func_def)

    mod = ast.Module(body=[func_def], type_ignores=[])
    compiled = compile(mod, filename="<reval>", mode="exec")

    exec(compiled, {}, locs)

    start = util.time.usec()
    try:
        result = await locs["__reval_func__"](**kwargs)
        end = util.time.usec()
        elapsed = end - start

        if isinstance(result, list):
            result = [await r if hasattr(r, "__await__") else r for r in result]
            result = [r for r in result if r is not None]
            if len(result) == 1:
                return result[0], elapsed, None
            elif not result:
                return None, elapsed, None
            return result, elapsed, None

        return result, elapsed, None

    except Exception as e:
        end = util.time.usec()
        elapsed = end - start
        return None, elapsed, e


class Debug(module.Module):
    name: ClassVar[str] = "Debug"

    async def on_load(self):
        self._log_cache = ""

    @command.desc("Evaluate code")
    @command.usage("[code snippet]")
    @command.alias("exec", "e")
    async def cmd_eval(self, ctx: command.Context) -> Optional[str]:
        if not ctx.input:
            return "Give me code to evaluate."

        code = ctx.msg.content.markdown.split(maxsplit=1)[1]
        out_buf = io.StringIO()

        async def send(*args: Any, **kwargs: Any) -> pyrogram.types.Message:
            return await ctx.msg.reply(*args, **kwargs)

        def _print(*args: Any, **kwargs: Any) -> None:
            if "file" not in kwargs:
                kwargs["file"] = out_buf
            return print(*args, **kwargs)

        eval_vars = {
            "self": self,
            "ctx": ctx,
            "bot": self.bot,
            "loop": self.bot.loop,
            "client": self.bot.client,
            "helper": self.bot.client_helper,
            "commands": self.bot.commands,
            "listeners": self.bot.listeners,
            "modules": self.bot.modules,
            "stdout": out_buf,
            "context": ctx,
            "msg": ctx.msg,
            "message": ctx.msg,
            "db": self.bot.db,
            "http": self.bot.http,
            "replied": ctx.reply_msg,
            "user": (ctx.reply_msg or ctx.msg).from_user,
            "send": send,
            "print": _print,
            "inspect": inspect,
            "os": os,
            "re": re,
            "reval": reval,
            "sys": sys,
            "traceback": traceback,
            "pyrogram": pyrogram,
            "enums": pyrogram.enums,
            "types": pyrogram.types,
            "raw": pyrogram.raw,
            "path": aiopath.AsyncPath,
            "command": command,
            "module": module,
            "util": util,
        }

        start_time = util.time.usec()
        try:
            with redirect_stdout(out_buf):
                result, elapsed, exception = await reval(code, globals(), **eval_vars)
                prefix = "" if exception is None else "⚠️ Error executing snippet\n\n"
                if exception is not None:
                    result = str(exception)
        except Exception as e:
            end_time = util.time.usec()
            elapsed = end_time - start_time
            prefix = "⚠️ Error executing snippet\n\n"
            result = e

        if not out_buf.getvalue() or result is not None:
            print(result, file=out_buf)

        el_str = util.time.format_duration_us(elapsed)
        out = out_buf.getvalue()
        if out.endswith("\n"):
            out = out[:-1]
        if not out.strip():
            out = "[No Output]"

        respond_text = f"""{prefix}Input:
<code>{escape(code)}</code>\n
Output:
<code>{escape(out)}</code>\n
<b>⏱ {el_str}</b>"""

        if len(respond_text) > 2048:
            if len(out) > 1024:
                async with self.bot.http.post(
                    "https://paste.rs", data=out.encode()
                ) as resp:
                    paste_url = await resp.text()
                respond_text = f"""{prefix}<b>In</b>:
<code>{escape(code[:512] + '...' if len(code) > 1024 else code)}</code>\n
Out:
<code>{escape(out[:1024] + '...')}</code>\n
<b><a href={paste_url}>⏱ {el_str}</a></b>"""

        await ctx.respond(respond_text, parse_mode=pyrogram.enums.ParseMode.HTML)

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
