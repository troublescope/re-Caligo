import base64
from io import BytesIO
from typing import ClassVar

from pyrogram import errors
from pyrogram.types import Message

from caligo import command, module


class Quote(module.Module):
    name: ClassVar[str] = "Quote"
    files_cache: ClassVar[dict] = {}

    @command.desc("Generate a quote sticker/image")
    @command.alias("q")
    @command.usage(
        "[count] [--bg color] [--me] [--nr] [--png] [--scale number] [--width number] [--height number] [--emoji brand] (reply to a message)"
    )
    async def cmd_quote(self, ctx: command.Context) -> None:
        if not ctx.reply_msg:
            await ctx.respond("⚠️ Reply to a message to make a quote.")
            return

        # parse count
        count = 1
        if ctx.input.strip().isdigit():
            count = max(1, min(int(ctx.input.strip()), 15))

        # flags
        format_type = "png" if "png" in ctx.flags else "webp"
        send_me = "me" in ctx.flags
        no_reply = "nr" in ctx.flags or "noreply" in ctx.flags
        bg_color = ctx.flags.get("bg", "#000000")  # default black
        scale = int(ctx.flags.get("scale", "2"))
        width = int(ctx.flags.get("width", "512"))
        height = int(ctx.flags.get("height", "768"))
        emoji_brand = ctx.flags.get("emoji", "apple")

        # collect messages (reply + next N)
        msgs = [ctx.reply_msg]
        if count > 1:
            async for m in self.bot.client.get_chat_history(
                ctx.chat.id, offset_id=ctx.reply_msg.id, limit=count - 1, reverse=True
            ):
                if not m.empty:
                    if no_reply:
                        m.reply_to_message = None
                    msgs.append(m)
        msgs.reverse()

        # feedback
        wait_msg = await ctx.respond("⏳ Generating…")

        # payload matching quote-api format
        payload = {
            "backgroundColor": bg_color,
            "width": width,
            "height": height,
            "scale": scale,
            "format": format_type,
            "ext": format_type,
            "emojiBrand": emoji_brand,
            "messages": [await self.render_message(self.bot.client, m) for m in msgs],
        }

        url = f"https://bot.lyo.su/quote/generate.{format_type}"
        try:
            async with self.bot.http.post(url, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    await wait_msg.edit(f"❌ Quote API error:\n<code>{text}</code>")
                    return
                content = await resp.read()
        except Exception as e:
            await wait_msg.edit(f"❌ Request failed: <code>{e}</code>")
            return

        bio = BytesIO(content)
        bio.name = f"quote.{format_type}"

        try:
            if format_type == "png":
                await self.bot.client.send_document(
                    "me" if send_me else ctx.chat.id, bio
                )
            else:
                await self.bot.client.send_sticker(
                    "me" if send_me else ctx.chat.id, bio
                )
        except errors.RPCError as e:
            await ctx.respond(f"❌ {e}")
        finally:
            await wait_msg.delete(revoke=True)

    @command.desc("Generate a fake quote")
    @command.alias("fq")
    @command.usage(
        "[--bg color] [--me] [--nr] [--png] [--text <text>] [--scale number] [--width number] [--height number] [--emoji brand] (reply to a message)"
    )
    async def cmd_fakequote(self, ctx: command.Context) -> None:
        if not ctx.reply_msg:
            await ctx.respond("⚠️ Reply to a message to fake-quote.")
            return

        # get fake text from flag first, then fallback to input
        fake_text = ctx.flags.get("text") or ctx.input.strip()
        if not fake_text:
            await ctx.respond("⚠️ Fake quote text is empty.")
            return

        # flags
        format_type = "png" if "png" in ctx.flags else "webp"
        send_me = "me" in ctx.flags
        no_reply = "nr" in ctx.flags or "noreply" in ctx.flags
        bg_color = ctx.flags.get("bg", "#000000")  # default black
        scale = int(ctx.flags.get("scale", "2"))
        width = int(ctx.flags.get("width", "512"))
        height = int(ctx.flags.get("height", "768"))
        emoji_brand = ctx.flags.get("emoji", "apple")

        # clone replied message
        q_message = ctx.reply_msg
        q_message.text = fake_text
        q_message.entities = None
        if no_reply:
            q_message.reply_to_message = None

        wait_msg = await ctx.respond("⏳ Generating…")

        payload = {
            "backgroundColor": bg_color,
            "width": width,
            "height": height,
            "scale": scale,
            "format": format_type,
            "ext": format_type,
            "emojiBrand": emoji_brand,
            "messages": [await self.render_message(self.bot.client, q_message)],
        }

        url = f"https://bot.lyo.su/quote/generate.{format_type}"
        try:
            async with self.bot.http.post(url, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    await wait_msg.edit(f"❌ Quote API error:\n<code>{text}</code>")
                    return
                content = await resp.read()
        except Exception as e:
            await wait_msg.edit(f"❌ Request failed: <code>{e}</code>")
            return

        bio = BytesIO(content)
        bio.name = f"quote.{format_type}"

        try:
            if format_type == "png":
                await self.bot.client.send_document(
                    "me" if send_me else ctx.chat.id, bio
                )
            else:
                await self.bot.client.send_sticker(
                    "me" if send_me else ctx.chat.id, bio
                )
        except errors.RPCError as e:
            await ctx.respond(f"❌ {e}")
        finally:
            await wait_msg.delete(revoke=True)

    async def render_message(self, app, message: Message) -> dict:
        async def get_file(file_id) -> str:
            if file_id in self.files_cache:
                return self.files_cache[file_id]
            content = await app.download_media(file_id, in_memory=True)
            data = base64.b64encode(bytes(content.getbuffer())).decode()
            self.files_cache[file_id] = data
            return data

        # Initialize result
        result = {}

        # User/author information
        author = {"id": 0, "name": "Unknown"}
        if message.from_user:
            u = message.from_user
            author["id"] = u.id
            author["first_name"] = u.first_name or ""
            if u.last_name:
                author["last_name"] = u.last_name
            author["name"] = self.get_full_name(u)
            if u.username:
                author["username"] = u.username

            # Avatar handling
            if u.photo:
                try:
                    await get_file(u.photo.big_file_id)
                    author["photo"] = {"big_file_id": u.photo.big_file_id}
                except:
                    pass

        elif message.sender_chat:
            c = message.sender_chat
            author["id"] = c.id
            author["name"] = c.title or "Unknown Chat"
            if c.username:
                author["username"] = c.username

            # Chat photo handling
            if c.photo:
                try:
                    await get_file(c.photo.big_file_id)
                    author["photo"] = {"big_file_id": c.photo.big_file_id}
                except:
                    pass

        result["from"] = author
        result["avatar"] = True

        # Message text
        text = ""
        if message.photo:
            text = message.caption or ""
        elif message.poll:
            text = self.get_poll_text(message.poll)
        elif message.sticker:
            text = (message.sticker.emoji or "🔸") + " Sticker"
        elif message.voice:
            text = "🎵 Voice message"
        elif message.video_note:
            text = "🎥 Video message"
        elif message.video:
            text = "🎬 Video" + ("\n" + message.caption if message.caption else "")
        elif message.audio:
            text = "🎵 Audio" + self.get_audio_text(message.audio)
        elif message.document:
            text = "📄 Document" + ("\n" + message.caption if message.caption else "")
        elif message.animation:
            text = "🎞 Animation" + ("\n" + message.caption if message.caption else "")
        elif message.location:
            text = f"📍 Location: {message.location.latitude}, {message.location.longitude}"
        elif message.venue:
            text = f"📍 {message.venue.title}\n{message.venue.address}"
        elif message.contact:
            text = f"👤 Contact: {message.contact.first_name}"
            if message.contact.last_name:
                text += f" {message.contact.last_name}"
            text += f"\n{message.contact.phone_number}"
        elif message.dice:
            text = f"{message.dice.emoji} Dice: {message.dice.value}"
        else:
            text = message.text or message.caption or ""

        result["text"] = text[:4096]  # Limit to API maximum

        # Entities - supporting all Telegram entity types
        entities = []
        if message.entities or message.caption_entities:
            msg_entities = message.entities or message.caption_entities or []
            for entity in msg_entities:
                entity_dict = {
                    "offset": entity.offset,
                    "length": entity.length,
                    "type": str(entity.type).split(".")[-1].lower(),
                }

                # Handle special entity types with additional data
                if entity.type.name == "TEXT_LINK":
                    entity_dict["url"] = entity.url
                elif entity.type.name == "TEXT_MENTION":
                    entity_dict["user"] = {
                        "id": entity.user.id,
                        "first_name": entity.user.first_name,
                        "is_bot": entity.user.is_bot,
                    }
                    if entity.user.last_name:
                        entity_dict["user"]["last_name"] = entity.user.last_name
                    if entity.user.username:
                        entity_dict["user"]["username"] = entity.user.username
                elif entity.type.name == "CUSTOM_EMOJI":
                    entity_dict["custom_emoji_id"] = entity.custom_emoji_id
                elif entity.type.name == "PRE":
                    if entity.language:
                        entity_dict["language"] = entity.language

                entities.append(entity_dict)

        result["entities"] = entities

        # Media handling
        if message.photo:
            try:
                await get_file(message.photo.file_id)
                result["media"] = {"file_id": message.photo.file_id}
                result["mediaType"] = "photo"
            except:
                pass

        elif message.sticker:
            try:
                await get_file(message.sticker.file_id)
                result["media"] = {
                    "file_id": message.sticker.file_id,
                    "width": message.sticker.width,
                    "height": message.sticker.height,
                    "is_animated": message.sticker.is_animated or False,
                }
                result["mediaType"] = "sticker"
            except:
                pass

        elif message.video:
            try:
                result["media"] = {
                    "file_id": message.video.file_id,
                    "width": message.video.width,
                    "height": message.video.height,
                }
                result["mediaType"] = "video"
            except:
                pass

        elif message.animation:
            try:
                result["media"] = {
                    "file_id": message.animation.file_id,
                    "width": message.animation.width,
                    "height": message.animation.height,
                }
                result["mediaType"] = "animation"
            except:
                pass

        # Voice message handling
        if message.voice:
            try:
                # Note: Pyrogram doesn't provide waveform data directly
                # This would need to be extracted from the voice file if needed
                result["voice"] = {"duration": message.voice.duration}
            except:
                pass

        # Reply message handling
        reply = {}
        r = message.reply_to_message
        if r and not r.empty:
            if r.from_user:
                reply["name"] = self.get_full_name(r.from_user)
                reply["from"] = {
                    "id": r.from_user.id,
                    "first_name": r.from_user.first_name or "",
                    "name": self.get_full_name(r.from_user),
                }
                if r.from_user.last_name:
                    reply["from"]["last_name"] = r.from_user.last_name
                if r.from_user.username:
                    reply["from"]["username"] = r.from_user.username
            elif r.sender_chat:
                reply["name"] = r.sender_chat.title or "Unknown Chat"
                reply["from"] = {
                    "id": r.sender_chat.id,
                    "name": r.sender_chat.title or "Unknown Chat",
                }

            reply["text"] = self.get_reply_text(r)
            reply["chatId"] = r.chat.id

            # Reply entities
            reply_entities = []
            if r.entities or r.caption_entities:
                msg_entities = r.entities or r.caption_entities or []
                for entity in msg_entities:
                    reply_entities.append(
                        {
                            "offset": entity.offset,
                            "length": entity.length,
                            "type": str(entity.type).split(".")[-1].lower(),
                        }
                    )
            reply["entities"] = reply_entities

        if reply:
            result["replyMessage"] = reply

        return result

    def get_full_name(self, user) -> str:
        name = user.first_name or ""
        if user.last_name:
            name += " " + user.last_name
        return name.strip() or "Unknown"

    def get_audio_text(self, audio) -> str:
        if audio.title and audio.performer:
            return f" ({audio.title} — {audio.performer})"
        elif audio.title:
            return f" ({audio.title})"
        elif audio.performer:
            return f" ({audio.performer})"
        else:
            return ""

    def get_reply_text(self, reply: Message) -> str:
        if reply.photo:
            return "📷 Photo" + ("\n" + reply.caption if reply.caption else "")
        if reply.poll:
            return self.get_poll_text(reply.poll)
        if reply.sticker:
            return (reply.sticker.emoji or "🔸") + " Sticker"
        if reply.voice:
            return "🎵 Voice message"
        if reply.video_note:
            return "🎥 Video message"
        if reply.video:
            return "🎬 Video" + ("\n" + reply.caption if reply.caption else "")
        if reply.audio:
            return "🎵 Audio" + self.get_audio_text(reply.audio)
        if reply.document:
            return "📄 Document" + ("\n" + reply.caption if reply.caption else "")
        if reply.animation:
            return "🎞 Animation" + ("\n" + reply.caption if reply.caption else "")
        if reply.location:
            return f"📍 Location"
        if reply.venue:
            return f"📍 {reply.venue.title}"
        if reply.contact:
            return f"👤 Contact: {reply.contact.first_name}"
        if reply.dice:
            return f"{reply.dice.emoji} Dice"
        return reply.text or reply.caption or "unsupported message"

    def get_poll_text(self, poll) -> str:
        text = "📊 Poll\n" + poll.question + "\n"
        for option in poll.options:
            text += f"- {option.text}"
            if option.voter_count > 0:
                text += f" ({option.voter_count} voted)"
            text += "\n"
        text += f"Total: {poll.total_voter_count} voted"
        return text
