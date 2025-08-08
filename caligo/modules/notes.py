import asyncio
from typing import Any, ClassVar, Dict, Optional, Tuple

from pymongo.asynchronous.collection import AsyncCollection
from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineQuery

from caligo import command, listener, module
from caligo.util.tg import (
    build_button,
    extract_message,
    generate_inline_result,
    generate_input_media,
)


class Notes(module.Module):
    name: ClassVar[str] = "Notes"
    db: AsyncCollection
    caches: Dict[str, Any]

    async def on_load(self) -> None:
        self.db = self.bot.db.get_collection(self.name.upper())
        self.caches = dict()

    @listener.filters(filters.regex(r"^note:\w+$"))
    async def on_inline_query(self, query: InlineQuery) -> None:
        _, name = query.query.split(":", maxsplit=1)
        data = await self._get_note(name)
        if not data:
            return

        resp = generate_inline_result(*data)
        await query.answer(**resp)

    @listener.filters(filters.regex(r"^note:\w+$"))
    async def on_callback_query(self, query: CallbackQuery) -> None:
        _, name = query.data.split(":", maxsplit=1)
        data = await self._get_note(name)
        if not data:
            await query.answer("Note not found.", show_alert=True)
            return

        _type, _text, _file, _btns = data
        if _type == "text":
            await query.edit_message_text(_text, reply_markup=_btns)
        else:
            resp = generate_input_media(_type, _text, _file, _btns)
            await query.edit_message_media(**resp)

    @command.desc("Retrieve a saved note by its name.")
    @command.usage("get <notename>")
    async def cmd_get(self, ctx: command.Context) -> None:
        if not ctx.input:
            await ctx.respond("__What should I get for you?__")
            return

        note_name = ctx.input.split()[0]
        note_data = await self._get_note(note_name)
        if not note_data:
            return await ctx.respond(f"Note `#{note_name}` not found.")

        _type, _text, _file, _btns = note_data
        if _btns:
            try:
                bot_results = await self.bot.client.get_inline_bot_results(
                    self.bot.client_helper.me.username, f"note:{note_name}"
                )
                if bot_results.results:
                    await ctx.msg.delete()
                    await self.bot.client.send_inline_bot_result(
                        ctx.msg.chat.id, bot_results.query_id, bot_results.results[0].id
                    )
            except Exception:
                await ctx.respond("Error: Could not send note via inline mode.")
        elif _file:
            if _type == "sticker":
                await asyncio.gather(ctx.msg.reply_sticker(_file), ctx.msg.delete())
            else:
                params = generate_input_media(_type, _text, _file, _btns)
                await ctx.msg.edit_media(**params)
        else:
            await ctx.respond(_text)

    @command.desc(
        "Save a new note by name. You can also reply to a message to save it."
    )
    @command.alias("addnote")
    @command.usage("save <notename> <note text> or reply to media/text")
    async def cmd_save(self, ctx: command.Context) -> None:
        if (
            not ctx.msg.reply_to_message
            and len(ctx.flags) < 2
            or ctx.msg.reply_to_message
            and len(ctx.flags) < 1
        ):
            text = (
                "Invalid arguments to save a note.\n\n"
                "<blockquote expandable><b>Usage:</b> save <code>notename note text</code> or reply to media/text.\n\n"
                "<b>Button formatting:</b>\n"
                "•  [Label](buttonurl://https://example.com)\n"
                "•  [Label](buttoncopy://Copied Text)\n"
                "•  [Btn1](buttonurl://url1)\n"
                "•  [Btn2](buttonurl://url2:same)\n"
                "Use :same to place buttons on the same row.</blockquote>"
            )
            await ctx.msg.edit(text, parse_mode=ParseMode.HTML)
            return

        trigger = ctx.flags.get("notename") or next(iter(ctx.flags), None)
        if not trigger:
            await ctx.respond("You must specify a note name.")
            return

        if trigger.startswith("#") or "." in trigger or "$" in trigger:
            await ctx.respond("Trigger cannot contain '#', '.', or '$' characters.")
            return

        (_type, _text, _file, _btns) = extract_message(ctx.msg)

        if _file and _btns:
            _file = await self._file_id(_file)

        data = {"type": _type, "text": _text, "file": _file, "btns": _btns}

        await asyncio.gather(
            self.db.update_one(
                {"_id": 0}, {"$set": {f"notes.{trigger}": data}}, upsert=True
            ),
            ctx.respond(f"Note **{trigger}** has been saved."),
        )

        _btns = build_button(_btns)
        self.caches[trigger] = (_type, _text, _file, _btns)

    @command.desc("List all saved notes")
    async def cmd_notes(self, ctx: command.Context) -> None:
        doc = await self.db.find_one({"_id": 0}, {"notes": 1})
        if not doc or "notes" not in doc or not doc["notes"]:
            await ctx.respond("There are no saved notes.")
            return

        note_list = sorted(doc["notes"].keys())
        response = "**Saved Notes**:\n" + "\n".join(
            f"• `#{name}`" for name in note_list
        )
        await ctx.respond(response)

    @command.desc("Delete a saved note by name.")
    @command.alias("clear")
    @command.usage("delnote <notename>")
    async def cmd_delnote(self, ctx: command.Context) -> None:
        if not ctx.input:
            await ctx.respond("Please provide the note name to delete.")
            return

        name = ctx.input.strip().split()[0]
        result = await self.db.update_one({"_id": 0}, {"$unset": {f"notes.{name}": ""}})
        if result.modified_count > 0:
            self.caches.pop(name, None)
            await ctx.respond(f"Note `#{name}` has been deleted.")
        else:
            await ctx.respond("That note does not exist.")

    @command.desc("Delete all saved notes.")
    @command.alias("clearnotes")
    async def cmd_clearallnotes(self, ctx: command.Context) -> None:
        result = await self.db.update_one(
            {"_id": 0, "notes": {"$exists": True, "$ne": {}}}, {"$set": {"notes": {}}}
        )
        if result.modified_count > 0:
            self.caches.clear()
            await ctx.respond("All notes have been deleted.")
        else:
            await ctx.respond("There are no notes to delete.")

    async def _get_note(
        self, name: str
    ) -> Optional[Tuple[str, str, str, InlineKeyboardMarkup]]:
        if name in self.caches:
            return self.caches[name]

        doc = await self.db.find_one({"_id": 0}, {f"notes.{name}": 1})
        if not doc or "notes" not in doc or name not in doc["notes"]:
            return None

        note = doc["notes"][name]
        buttons = build_button(note.get("buttons", []))
        result = (note.get("type"), note.get("text"), note.get("file"), buttons)
        self.caches[name] = result
        return result

    async def _file_id(self, file_id: str) -> Optional[str]:
        try:
            usr_msg = await self.bot.client.send_cached_media(
                self.bot.log_chat, file_id
            )
            bot_msg = await self.bot.client_helper.get_messages(
                usr_msg.chat.id, usr_msg.id
            )
            asyncio.create_task(usr_msg.delete())

            media_type = bot_msg.media.value
            media = getattr(bot_msg, media_type)

            if bot_msg.photo:
                return media.sizes[-1].file_id
            return media.file_id
        except Exception:
            return file_id
