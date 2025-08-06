import uuid
from typing import Any, ClassVar, Optional

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


class NoteNotFoundError(Exception):
    """Raised when a requested note is not found."""


class InvalidNoteNameError(Exception):
    """Raised when note name contains invalid characters."""


class LogChatNotSetError(Exception):
    """Raised when log chat is required but not set."""


class Notes(module.Module):
    name: ClassVar[str] = "Notes"
    helpable: ClassVar[bool] = True

    db: database.AsyncCollection

    # Constants
    INVALID_CHARS = {"#", ".", "$"}
    MAX_DESCRIPTION_LENGTH = 50

    async def on_load(self) -> None:
        """Initialize the module."""
        self.db = self.bot.db.get_collection(self.name.upper())
        await self._setup_send_methods()
        self.inline_state: dict[str, list] = {}

    async def _setup_send_methods(self) -> None:
        """Setup message sending methods mapping."""
        self.send_methods = {
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
        """Handle inline queries for notes."""
        try:
            results = self.inline_state.get(event.query)
            if not results:
                self.bot.log.debug(f"No results found for inline query: {event.query}")
                await event.stop_propagation()
                return

            self.bot.log.debug(
                f"Answering inline query {event.query} with {len(results)} results"
            )
            await event.answer(results=results, cache_time=0)

        except Exception as e:
            self.bot.log.error(
                f"Failed to answer inline query {event.query}: {e}", exc_info=True
            )
        finally:
            # Clean up state
            self.inline_state.pop(event.query, None)

    @listener.priority(95)
    @listener.filters(filters.regex(r"^#[\w\-]+(?!\n)$") & filters.me)
    async def on_message(self, message: Message) -> None:
        """Handle hashtag note retrieval."""
        trigger = message.text or message.caption
        if trigger:
            note_name = trigger.lstrip("#")
            await self._get_note_safe(message, note_name)

    async def _get_note_safe(
        self, message: Message, name: str, noformat: bool = False
    ) -> None:
        """Safely get a note with proper error handling."""
        try:
            await self._get_note(message, name, noformat)
        except NoteNotFoundError:
            await message.edit(f"Note '{name}' not found.")
        except LogChatNotSetError:
            await self.bot.client.send_message(
                message.chat.id,
                "Log chat is not set. Use <code>.setlogchat here</code> in a group or channel.",
                parse_mode=ParseMode.HTML,
                reply_to_message_id=message.id,
            )
        except Exception as e:
            self.bot.log.error(f"Error retrieving note '{name}': {e}")
            await message.edit(f"Failed to retrieve note '{name}'. Please try again.")

    async def _get_note(
        self, message: Message, name: str, noformat: bool = False
    ) -> None:
        """Get and send a note."""
        note_data = await self._fetch_note_from_db(name)
        if not note_data:
            raise NoteNotFoundError(f"Note '{name}' not found")

        await message.delete(revoke=True)

        note = note_data["notes"][name]
        button = note.get("button")
        note_type = note["type"]
        text = note["text"] or name
        content = note.get("content")

        # Validate log chat requirement for buttons (except stickers)
        if button and note_type != Types.STICKER.value and not self.bot.log_chat:
            raise LogChatNotSetError("Log chat required for notes with buttons")

        reply_to = self._get_reply_to_id(message)

        if noformat:
            await self._send_note_noformat(
                message.chat, note_type, text, content, button, reply_to
            )
        else:
            await self._send_note_formatted(
                message.chat, note_type, text, content, button, reply_to, name
            )

    async def _fetch_note_from_db(self, name: str) -> Optional[dict]:
        """Fetch note data from database."""
        return await self.db.find_one(
            {"_id": 0, f"notes.{name}": {"$exists": True}}, {f"notes.{name}": 1}
        )

    def _get_reply_to_id(self, message: Message) -> int:
        """Get the appropriate reply-to message ID."""
        return message.reply_to_message.id if message.reply_to_message else message.id

    async def _send_note_noformat(
        self,
        chat,
        note_type: int,
        text: str,
        content: Optional[str],
        button: Optional[list],
        reply_to: int,
    ) -> None:
        """Send note without formatting."""
        btn_text = "\n\n" + revert_button(button) if button else ""
        full_text = text + btn_text

        try:
            if note_type in {Types.TEXT.value, Types.BUTTON_TEXT.value}:
                await self.send_methods[note_type](
                    chat.id,
                    full_text,
                    disable_web_page_preview=True,
                    reply_to_message_id=reply_to,
                    parse_mode=ParseMode.DISABLED,
                )
            elif note_type == Types.STICKER.value:
                await self.send_methods[note_type](
                    chat.id,
                    content,
                    reply_to_message_id=reply_to,
                )
            else:
                await self.send_methods[note_type](
                    chat.id,
                    str(content),
                    caption=full_text,
                    reply_to_message_id=reply_to,
                    parse_mode=ParseMode.DISABLED,
                )
        except MediaEmpty:
            await self.bot.client.send_message(
                chat.id,
                "Your note has expired...",
                message_thread_id=getattr(chat, "message_thread_id", None),
            )
        except MessageEmpty:
            self.bot.log.warning("Attempted to send empty message")

    async def _send_note_formatted(
        self,
        chat,
        note_type: int,
        text: str,
        content: Optional[str],
        button: Optional[list],
        reply_to: int,
        name: str,
    ) -> None:
        """Send note with formatting and buttons."""
        # For stickers, send directly without inline processing
        if note_type == Types.STICKER.value:
            await self._send_sticker_note(chat, content, reply_to)
            return

        btn_markup = None
        if button:
            try:
                btn_markup = await util.run_sync(build_button, button)
            except ButtonUrlInvalid as e:
                self.bot.log.error(f"Invalid button for note '{name}': {e}")
                await self._log_button_error(name, str(e))
            except Exception as e:
                self.bot.log.error(
                    f"Error building button for note '{name}': {e}", exc_info=True
                )

        if not content:
            await self._send_text_note(
                chat, text, btn_markup, reply_to, note_type, name
            )
        else:
            await self._send_media_note(chat, text, content, btn_markup, reply_to, name)

    async def _send_sticker_note(self, chat, content: str, reply_to: int) -> None:
        """Send sticker note directly without inline processing."""
        try:
            await self.send_methods[Types.STICKER.value](
                chat.id,
                content,
                reply_to_message_id=reply_to,
            )
        except MediaEmpty:
            await self.bot.client.send_message(
                chat.id,
                "Your sticker note has expired...",
                message_thread_id=getattr(chat, "message_thread_id", None),
            )
        except Exception as e:
            self.bot.log.error(f"Failed to send sticker note: {e}")
            await self.bot.client.send_message(
                chat.id,
                "Failed to send sticker note.",
                reply_to_message_id=reply_to,
            )

    async def _send_text_note(
        self, chat, text: str, btn_markup, reply_to: int, note_type: int, name: str
    ) -> None:
        """Send text-only note."""
        if btn_markup:
            try:
                await self._send_inline_text_note(
                    chat, text, btn_markup, reply_to, name
                )
            except Exception as e:
                self.bot.log.error(f"Failed to send inline text note '{name}': {e}")
                # Fallback to regular message
                await self.send_methods[note_type](
                    chat.id,
                    text,
                    reply_to_message_id=reply_to,
                    disable_web_page_preview=True,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=btn_markup,
                )
        else:
            await self.send_methods[note_type](
                chat.id,
                text,
                reply_to_message_id=reply_to,
                disable_web_page_preview=True,
                parse_mode=ParseMode.MARKDOWN,
            )

    async def _send_inline_text_note(
        self, chat, text: str, btn_markup, reply_to: int, name: str
    ) -> None:
        """Send text note using inline bot."""
        key = f"notes({uuid.uuid4().hex})"
        description = text[: self.MAX_DESCRIPTION_LENGTH] + (
            "..." if len(text) > self.MAX_DESCRIPTION_LENGTH else ""
        )

        self.inline_state[key] = [
            InlineQueryResultArticle(
                title=f"Note: {name}",
                input_message_content=InputTextMessageContent(
                    message_text=text,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True,
                ),
                reply_markup=btn_markup,
                description=description,
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

    async def _send_media_note(
        self, chat, text: str, content: str, btn_markup, reply_to: int, name: str
    ) -> None:
        """Send media note using inline bot."""
        # Send temporary message to log chat
        tmp_msg = await self.bot.client.send_cached_media(
            self.bot.log_chat, content, caption=text
        )

        try:
            msg_bot = await self.bot.client_helper.get_messages(
                self.bot.log_chat, tmp_msg.id
            )

            inline = await self._generate_inline_result_async(msg_bot, btn_markup)
            key = f"notes({uuid.uuid4().hex})"
            self.inline_state[key] = [inline]

            results = await self.bot.client.get_inline_bot_results(
                self.bot.client_helper.me.username, key
            )
            await self.bot.client.send_inline_bot_result(
                chat_id=chat.id,
                query_id=results.query_id,
                result_id=results.results[0].id,
                reply_to_message_id=reply_to,
            )
        except (IndexError, ButtonUrlInvalid) as e:
            self.bot.log.error(f"Failed to send media note '{name}': {e}")
            # Fallback to text message
            await self.bot.client.send_message(
                chat.id,
                text,
                reply_to_message_id=reply_to,
                disable_web_page_preview=True,
                parse_mode=ParseMode.MARKDOWN,
            )
        finally:
            await tmp_msg.delete()

    async def _generate_inline_result_async(self, msg_bot, btn_markup) -> Any:
        """Generate inline result asynchronously."""
        return await util.run_sync(generate_inline_result, msg_bot, btn_markup)

    async def _log_button_error(self, note_name: str, error: str) -> None:
        """Log button validation errors."""
        if self.bot.log_chat:
            await self.bot.client.send_message(
                self.bot.log_chat,
                f"⚠️ <b>Invalid button detected for note:</b> <code>{note_name}</code>\n"
                f"<b>Error:</b> <code>{error}</code>",
                parse_mode=ParseMode.HTML,
            )

    def _validate_note_name(self, name: str) -> None:
        """Validate note name."""
        if not name:
            raise InvalidNoteNameError("Note name cannot be empty")

        if any(char in name for char in self.INVALID_CHARS):
            raise InvalidNoteNameError(
                f"Note name cannot contain: {', '.join(self.INVALID_CHARS)}"
            )

    @command.desc("Retrieve a saved note by its name.")
    @command.usage("get <notename> [noformat]")
    async def cmd_get(self, ctx: command.Context) -> None:
        """Get a note command."""
        if not ctx.input:
            return await ctx.respond("__What should I get for you?__")

        args = ctx.input.split()
        note_name = args[0] if args else None
        noformat = "noformat" in args

        if not note_name or note_name == "noformat":
            return await ctx.respond("Please specify a note name to retrieve.")

        await self._get_note_safe(ctx.msg, note_name, noformat=noformat)

    @command.desc(
        "Save a new note by name. You can also reply to a message to save it."
    )
    @command.alias("addnote")
    @command.usage("save <notename> <note text> or reply to media/text")
    async def cmd_save(self, ctx: command.Context) -> None:
        """Save a note command."""
        try:
            trigger, note_data = await self._parse_save_command(ctx)
            await self._save_note_to_db(trigger, note_data)
            await ctx.respond(f"Note **{trigger}** has been saved.")
        except (InvalidNoteNameError, ValueError) as e:
            await ctx.respond(str(e))
        except Exception as e:
            self.bot.log.error(f"Error saving note: {e}")
            await ctx.respond("Failed to save note. Please try again.")

    async def _parse_save_command(self, ctx: command.Context) -> tuple[str, dict]:
        """Parse save command and extract note data."""
        # Validate arguments
        min_flags = 1 if ctx.msg.reply_to_message else 2
        if len(ctx.flags) < min_flags:
            raise ValueError(self._get_save_usage_text())

        # Get note name
        trigger = ctx.flags.get("notename") or next(iter(ctx.flags), None)
        if not trigger:
            raise InvalidNoteNameError("You must specify a note name.")

        # Validate note name
        self._validate_note_name(trigger)

        # Get note content
        text, note_type, content, buttons = await self._get_message_info_async(ctx.msg)

        return trigger, {
            "text": text,
            "type": note_type,
            "content": content,
            "button": buttons,
        }

    async def _get_message_info_async(self, message: Message) -> tuple:
        """Get message info asynchronously."""
        return await util.run_sync(get_message_info, message)

    def _get_save_usage_text(self) -> str:
        """Get usage text for save command."""
        return (
            "Invalid arguments to save a note.\n\n"
            "<blockquote expandable><b>Usage:</b> save <code>notename note text</code> or reply to media/text.\n\n"
            "<b>Button formatting:</b>\n"
            "•  [Label](buttonurl://https://example.com)\n"
            "•  [Label](buttoncopy://Copied Text)\n"
            "•  [Btn1](buttonurl://url1)\n"
            "•  [Btn2](buttonurl://url2:same)\n"
            "Use :same to place buttons on the same row.</blockquote>"
        )

    async def _save_note_to_db(self, trigger: str, note_data: dict) -> None:
        """Save note data to database."""
        await self.db.update_one(
            {"_id": 0},
            {"$set": {f"notes.{trigger}": note_data}},
            upsert=True,
        )

    @command.desc("List all saved notes")
    async def cmd_notes(self, ctx: command.Context) -> None:
        """List notes command."""
        try:
            notes = await self._get_all_notes()
            if not notes:
                return await ctx.respond("There are no saved notes.")

            note_list = sorted(notes.keys())
            response = "**Saved Notes**:\n\n"
            response += "\n".join(f"`#{name}`" for name in note_list)
            await ctx.respond(response)
        except Exception as e:
            self.bot.log.error(f"Error listing notes: {e}")
            await ctx.respond("Failed to list notes. Please try again.")

    async def _get_all_notes(self) -> dict:
        """Get all notes from database."""
        data = await self.db.find_one({"_id": 0})
        return data.get("notes", {}) if data else {}

    @command.desc("Delete a saved note by name.")
    @command.alias("clear")
    @command.usage("delnote <notename>")
    async def cmd_delnote(self, ctx: command.Context) -> None:
        """Delete note command."""
        if not ctx.input:
            return await ctx.respond("Please provide the note name to delete.")

        name = ctx.input.strip()

        try:
            if await self._delete_note_from_db(name):
                await ctx.respond(f"Note **{name}** has been deleted.")
            else:
                await ctx.respond("That note does not exist.")
        except Exception as e:
            self.bot.log.error(f"Error deleting note '{name}': {e}")
            await ctx.respond("Failed to delete note. Please try again.")

    async def _delete_note_from_db(self, name: str) -> bool:
        """Delete note from database. Returns True if note existed."""
        # Check if note exists
        data = await self.db.find_one({"_id": 0, f"notes.{name}": {"$exists": True}})
        if not data:
            return False

        # Delete the note
        await self.db.update_one({"_id": 0}, {"$unset": {f"notes.{name}": ""}})
        return True

    @command.desc("Delete all saved notes.")
    @command.alias("clearnotes")
    async def cmd_clearallnotes(self, ctx: command.Context) -> None:
        """Clear all notes command."""
        try:
            notes = await self._get_all_notes()
            if not notes:
                return await ctx.respond("There are no notes to delete.")

            await self.db.update_one({"_id": 0}, {"$unset": {"notes": ""}})
            await ctx.respond("All notes have been deleted.")
        except Exception as e:
            self.bot.log.error(f"Error clearing all notes: {e}")
            await ctx.respond("Failed to clear notes. Please try again.")
