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
from pyrogram.enums import ParseMode

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
            "sys": sys,
            "traceback": traceback,
            # Third-party modules
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

        try:
            with redirect_stdout(out_buf):
                result, elapsed, exception = await reval(code, globals(), **eval_vars)

                if exception is not None:
                    prefix = "⚠️ Error executing snippet\n\n"
                    result = str(exception)
                else:
                    prefix = ""

        except Exception as e:  # skipcq: PYL-W0703
            # This should only catch exceptions from reval itself, not from the executed code
            raise e

        # Always write result if no output has been collected thus far
        if not out_buf.getvalue() or result is not None:
            print(result, file=out_buf)

        el_str = util.time.format_duration_us(elapsed)

        out = out_buf.getvalue()
        # Strip only ONE final newline to compensate for our message formatting
        if out.endswith("\n"):
            out = out[:-1]

        respond_text = f"""{prefix}Input:
<code>{escape(code)}</code>\n
Output:
<code>{escape(out)}</code>\n\n
<b>{el_str}</b>"""

        if len(respond_text) > 2048:
            data = ""
            if len(code) > 1024:
                code = code[:1024] + "..."
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
<code>{escape(out)}</code>\n\n
<b><a href={paste_url}>{el_str}</a></b>"""

        await ctx.respond(
            respond_text,
            parse_mode=pyrogram.enums.parse_mode.ParseMode.HTML,
        )
