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

from caligo import command, module, util


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

    @command.desc("Evaluate code")
    @command.usage("[code snippet]")
    @command.alias("exec", "e")
    async def cmd_eval(self, ctx: command.Context) -> Optional[str]:
        if not ctx.input:
            return "Give me code to evaluate."

        code = ctx.msg.content.markdown.split(maxsplit=1)[1]

        out_buf = io.StringIO()

        # Message sending helper for convenience
        async def send(*args: Any, **kwargs: Any) -> pyrogram.types.Message:
            return await ctx.msg.reply(*args, **kwargs)

        # Print wrapper to capture output
        # We don't override sys.stdout to avoid interfering with other output
        def _print(*args: Any, **kwargs: Any) -> None:
            if "file" not in kwargs:
                kwargs["file"] = out_buf

            return print(*args, **kwargs)

        eval_vars = {
            # Contextual info
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
            # Convenience aliases
            "context": ctx,
            "msg": ctx.msg,
            "message": ctx.msg,
            "db": self.bot.db,
            "http": self.bot.http,
            "replied": ctx.reply_msg,
            "user": (ctx.reply_msg or ctx.msg).from_user,
            # Helper functions
            "send": send,
            "print": _print,
            # Built-in modules
            "inspect": inspect,
            "os": os,
            "re": re,
            "reval": reval,
            "sys": sys,
            "traceback": traceback,
            # Pyrogram
            "pyrogram": pyrogram,
            "enums": pyrogram.enums,
            "types": pyrogram.types,
            "raw": pyrogram.raw,
            # Aiopath
            "path": aiopath.AsyncPath,
            # Bot
            "command": command,
            "module": module,
            "util": util,
        }

        start_time = util.time.usec()
        try:
            with redirect_stdout(out_buf):
                result, elapsed, exception = await reval(code, globals(), **eval_vars)

                if exception is not None:
                    prefix = "⚠️ Error executing snippet\n\n"
                    result = str(exception)
                else:
                    prefix = ""

        except Exception as e:  # skipcq: PYL-W0703
            # Handle syntax errors and other exceptions from reval itself
            end_time = util.time.usec()
            elapsed = end_time - start_time
            prefix = "⚠️ Error executing snippet\n\n"
            result = e

        # Always write result if no output has been collected thus far
        if not out_buf.getvalue() or result is not None:
            print(result, file=out_buf)

        el_str = util.time.format_duration_us(elapsed)

        out = out_buf.getvalue()
        # Strip only ONE final newline to compensate for our message formatting
        if out.endswith("\n"):
            out = out[:-1]

        # Replace empty output with "[No Output]"
        if not out.strip():
            out = "[No Output]"

        respond_text = f"""{prefix}Input:
<code>{escape(code)}</code>\n
Output:
<code>{escape(out)}</code>\n
<b>⏱ {el_str}</b>"""

        if len(respond_text) > 2048:
            data = ""
            if len(code) > 1024:
                code = code[:512] + "..."
                data += f"Input:\n{code}"

            if len(out) > 1024:
                async with self.bot.http.post(
                    "https://paste.rs", data=out.encode()
                ) as resp:
                    paste_url = await resp.text()

                out = out[:1024] + "..."
                if data:
                    data += f"\n\nOutput:\n{out}"

                else:
                    data = out

            respond_text = f"""{prefix}<b>In</b>:
<code>{escape(code)}</code>\n
Out:
<code>{escape(out)}</code>\n
<b><a href={paste_url}>⏱ {el_str}</a></b>"""

        await ctx.respond(
            respond_text,
            parse_mode=pyrogram.enums.parse_mode.ParseMode.HTML,
        )
