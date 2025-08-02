import asyncio
import uuid
from typing import Any, Callable, ClassVar, Coroutine, MutableMapping, Optional

from pyrogram import filters
from pyrogram.enums.parse_mode import ParseMode
from pyrogram.errors import ButtonUrlInvalid, MediaEmpty, MessageEmpty
from pyrogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)

from caligo import command, listener, module, util
from caligo.core import database
from caligo.util.tg import (
    Types,
    build_button,
    generate_inline_result,
    get_message_info,
    revert_button,
)


class Notes(module.Module):
    name: ClassVar[str] = "Notes"
    helpable: ClassVar[bool] = True

    db: database.AsyncCollection
    SEND: MutableMapping[int, Callable[..., Coroutine[Any, Any, Optional[Message]]]]
    state: dict[str, list]
    log_chat: int

    async def on_load(self):
        self.db = self.bot.db.get_collection(self.name.upper())
        self.log_chat = self.bot.config["bot"]["log_chat"]

        self.state = {}
        self.SEND = {
            Types.TEXT.value: self.bot.client.send_message,
            Types.BUTTON_TEXT.value: self.bot.client.send_message,
            Types.DOCUMENT.value: self.bot.client.send_document,
            Types.PHOTO.value: self.bot.client.send_photo,
            Types.VIDEO.value: self.bot.client.send_video,
            Types.STICKER.value: self.bot.client.send_sticker,
            Types.AUDIO.value: self.bot.client.send_audio,
            Types.VOICE.value: self.bot.client.send_voice,
            Types.VIDEO_NOTE.value: self.bot.client.send_video_note,
            Types.ANIMATION.value: self.bot.client.send_animation,
        }

    @listener.filters(filters.regex(r"^notes\([a-f0-9]{32}\)$"))
    async def on_inline_query(self, event: InlineQuery) -> None:
        results = self.state.get(event.query)
        if not results:
            await event.stop_propagation()
            return
        try:
            await event.answer(results=results, cache_time=0)
        except Exception as e:
            await self.bot.client.send_message(
                self.log_chat,
                f"<b>Failed to send inline note result</b>\n"
                f"<b>Reason:</b> <code>{e}</code>",
                parse_mode=ParseMode.HTML,
            )
        finally:
            await asyncio.to_thread(self.state.pop, event.query, None)

    @listener.priority(95)
    @listener.filters(filters.regex(r"^#[\w\-]+(?!\n)$") & filters.me)
    async def on_message(self, message: Message) -> None:
        trigger = message.text or message.caption
        await self.get_note(message, trigger.lstrip("#"))

    async def get_note(
        self, message: Message, name: str, noformat: bool = False
    ) -> None:
        chat = message.chat
        reply_to = (
            message.reply_to_message.id if message.reply_to_message else message.id
        )

        data = await self.db.find_one(
            {"_id": 0, f"notes.{name}": {"$exists": True}}, {f"notes.{name}": 1}
        )
        if not data:
            return await message.edit(f"Notes with {name} is  not found.")
        await message.delete(revoke=True)

        note = data["notes"][name]
        button = note.get("button")
        types = note["type"]
        text = note["text"] or name
        content = note.get("content")

        if noformat:
            parse_mode = ParseMode.DISABLED
            btn_text = "\n\n" + revert_button(button) if button else ""
            keyb = None
            try:
                if types in {Types.TEXT, Types.BUTTON_TEXT}:
                    await self.SEND[types](
                        chat.id,
                        text + btn_text,
                        disable_web_page_preview=True,
                        reply_to_message_id=reply_to,
                        reply_markup=keyb,
                        parse_mode=parse_mode,
                    )
                elif types == Types.STICKER:
                    await self.SEND[types](
                        chat.id,
                        content,
                        reply_to_message_id=reply_to,
                        reply_markup=keyb,
                    )
                else:
                    await self.SEND[types](
                        chat.id,
                        str(content),
                        caption=text + btn_text,
                        reply_to_message_id=reply_to,
                        reply_markup=keyb,
                        parse_mode=parse_mode,
                    )
            except MediaEmpty:
                await self.bot.client.send_message(
                    chat.id,
                    "Your note has expired...",
                    message_thread_id=message.message_thread_id,
                )
            except MessageEmpty:
                pass
            return

        try:
            btn_markup = await util.run_sync(build_button, button) if button else None
        except ButtonUrlInvalid as e:
            await self.bot.client.send_message(
                self.log_chat,
                f"⚠️ <b>Invalid button detected for note:</b> <code>{name}</code>\n"
                f"<b>Error:</b> <code>{e}</code>",
                parse_mode=ParseMode.HTML,
            )
            btn_markup = None

        if not content:
            if btn_markup:
                try:
                    key = f"notes({uuid.uuid4().hex})"
                    self.state[key] = [
                        InlineQueryResultArticle(
                            title=f"Note: {name}",
                            input_message_content=InputTextMessageContent(
                                message_text=text,
                                parse_mode=ParseMode.MARKDOWN,
                                disable_web_page_preview=True,
                            ),
                            reply_markup=btn_markup,
                            description=text[:50] + ("..." if len(text) > 50 else ""),
                        )
                    ]
                    results = await self.bot.client.get_inline_bot_results(
                        self.bot.client_helper.me.username, key
                    )
                    await self.bot.client.send_inline_bot_result(
                        chat_id=chat.id,
                        query_id=results.query_id,
                        result_id=results.results[0].id,
                        reply_to_message_id=reply_to,
                    )
                except Exception as e:
                    await self.bot.client.send_message(
                        self.log_chat,
                        f"<b>Failed to send inline text note:</b> <code>{name}</code>\n<code>{e}</code>",
                        parse_mode=ParseMode.HTML,
                    )
                    await self.SEND[types](
                        chat.id,
                        text,
                        reply_to_message_id=reply_to,
                        disable_web_page_preview=True,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=btn_markup,
                    )
            else:
                await self.SEND[types](
                    chat.id,
                    text,
                    reply_to_message_id=reply_to,
                    disable_web_page_preview=True,
                    parse_mode=ParseMode.MARKDOWN,
                )
            return

        _tmp_msg = await self.bot.client.send_cached_media(
            self.log_chat, content, caption=text
        )
        _msgbot = await self.bot.client_helper.get_messages(self.log_chat, _tmp_msg.id)

        try:
            inline = await generate_inline_result(_msgbot, btn_markup)
            key = f"notes({uuid.uuid4().hex})"
            self.state[key] = [inline]
            results = await self.bot.client.get_inline_bot_results(
                self.bot.client_helper.me.username, key
            )

            await self.bot.client.send_inline_bot_result(
                chat_id=chat.id,
                query_id=results.query_id,
                result_id=results.results[0].id,
                reply_to_message_id=reply_to,
            )
        except (IndexError, ButtonUrlInvalid):
            await self.bot.client.send_message(
                chat.id,
                text,
                reply_to_message_id=reply_to,
                disable_web_page_preview=True,
                parse_mode=ParseMode.MARKDOWN,
            )
        finally:
            await _tmp_msg.delete()

    @command.desc("Retrieve a saved note by its name.")
    @command.usage("get <notename>")
    async def cmd_get(self, ctx: command.Context) -> None:
        if not ctx.input:
            return "__What should i get for you?__"

        note_name = ctx.flags.get("notename") or next(iter(ctx.flags), None)
        noformat = ctx.flags.get("noformat", False)

        if not note_name:
            return await ctx.respond("Please specify a note name to retrieve.")

        await self.get_note(ctx.msg, note_name, noformat=bool(noformat))

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

        text, types, content, buttons = await util.run_sync(get_message_info, ctx.msg)

        await self.db.update_one(
            {"_id": 0},
            {
                "$set": {
                    f"notes.{trigger}": {
                        "text": text,
                        "type": types,
                        "content": content if content else None,
                        "button": buttons,
                    },
                }
            },
            upsert=True,
        )
        await ctx.respond(f"Note **{trigger}** has been saved.")

    @command.desc("List all saved notes")
    async def cmd_notes(self, ctx: command.Context) -> None:
        data = await self.db.find_one({"_id": 0})
        if not data or not data.get("notes"):
            await ctx.respond("There are no saved notes.")
            return

        notes = data["notes"]
        note_list = sorted(notes.keys())
        response = "**Saved Notes**:\n\n"
        response += "\n".join(f"`#{name}`" for name in note_list)
        await ctx.respond(response)

    @command.desc("Delete a saved note by name.")
    @command.alias("clear")
    @command.usage("delnote <notename>")
    async def cmd_delnote(self, ctx: command.Context) -> None:
        if not ctx.input:
            await ctx.respond("Please provide the note name to delete.")
            return

        name = ctx.input
        data = await self.db.find_one({"_id": 0, f"notes.{name}": {"$exists": True}})
        if not data:
            await ctx.respond("That note does not exist.")
            return

        notes: MutableMapping[str, Any] = data["notes"]
        if name not in notes:
            await ctx.respond("Note does not exist in database.")
            return

        await self.db.update_one({"_id": 0}, {"$unset": {f"notes.{name}": ""}})
        await ctx.respond(f"Note **{name}** has been deleted.")
