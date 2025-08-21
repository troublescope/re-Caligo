import base64
from io import BytesIO
from typing import ClassVar

import requests
from pyrogram import errors
from pyrogram.types import Message

from caligo import command, module


class Quote(module.Module):
    name: ClassVar[str] = "Quote"
    files_cache: ClassVar[dict] = {}

    @command.desc("Generate a quote sticker/image")
    @command.alias("q")
    @command.usage("[count] [--bg color] [--me] [--nr] [--png] (reply to a message)")
    async def cmd_quote(self, ctx: command.Context) -> None:
        if not ctx.reply_msg:
            await ctx.respond("⚠️ Reply to a message to make a quote.")
            return

        # parse count
        count = 1
        if ctx.input.strip().isdigit():
            count = max(1, min(int(ctx.input.strip()), 15))

        # flags
        ext = "png" if "png" in ctx.flags else "webp"
        send_me = "me" in ctx.flags
        no_reply = "nr" in ctx.flags or "noreply" in ctx.flags
        bg_color = ctx.flags.get("bg", "#000000")  # default black

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

        # payload
        payload = {
            "messages": [await self.render_message(self.bot.client, m) for m in msgs],
            "quote_color": bg_color,
            "text_color": "#fff",
        }

        url = "https://quotes.fl1yd.su/generate"
        resp = requests.post(url, json=payload)
        if not resp.ok:
            await wait_msg.edit(f"❌ Quote API error:\n<code>{resp.text}</code>")
            return

        bio = BytesIO(resp.content)
        bio.name = f"quote.{ext}"

        try:
            if ext == "png":
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
        "[--bg color] [--me] [--nr] [--png] [--text <text>] (reply to a message)"
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
        ext = "png" if "png" in ctx.flags else "webp"
        send_me = "me" in ctx.flags
        no_reply = "nr" in ctx.flags or "noreply" in ctx.flags
        bg_color = ctx.flags.get("bg", "#000000")  # default black

        # clone replied message
        q_message = ctx.reply_msg
        q_message.text = fake_text
        q_message.entities = None
        if no_reply:
            q_message.reply_to_message = None

        wait_msg = await ctx.respond("⏳ Generating…")

        payload = {
            "messages": [await self.render_message(self.bot.client, q_message)],
            "quote_color": bg_color,
            "text_color": "#fff",
        }

        url = "https://quotes.fl1yd.su/generate"
        resp = requests.post(url, json=payload)
        if not resp.ok:
            await wait_msg.edit(f"❌ Quote API error:\n<code>{resp.text}</code>")
            return

        bio = BytesIO(resp.content)
        bio.name = f"quote.{ext}"

        try:
            if ext == "png":
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

        # text
        if message.photo:
            text = message.caption if message.caption else ""
        elif message.poll:
            text = self.get_poll_text(message.poll)
        elif message.sticker:
            text = ""
        else:
            text = self.get_reply_text(message)

        # media
        if message.photo:
            media = await get_file(message.photo.file_id)
        elif message.sticker:
            media = await get_file(message.sticker.file_id)
        else:
            media = ""

        # entities
        entities = []
        if message.entities:
            for entity in message.entities:
                entities.append(
                    {
                        "offset": entity.offset,
                        "length": entity.length,
                        "type": str(entity.type).split(".")[-1].lower(),
                    }
                )

        # author
        author = {"id": 0, "name": "Unknown", "rank": ""}
        if message.from_user:
            u = message.from_user
            author["id"] = u.id
            author["name"] = self.get_full_name(u)
            if u.photo:
                author["avatar"] = await get_file(u.photo.big_file_id)
            else:
                author["avatar"] = ""
        elif message.sender_chat:
            c = message.sender_chat
            author["id"] = c.id
            author["name"] = c.title
            if c.photo:
                author["avatar"] = await get_file(c.photo.big_file_id)
            else:
                author["avatar"] = ""

        # reply
        reply = {}
        r = message.reply_to_message
        if r and not r.empty:
            if r.from_user:
                reply["id"] = r.from_user.id
                reply["name"] = self.get_full_name(r.from_user)
            elif r.sender_chat:
                reply["id"] = r.sender_chat.id
                reply["name"] = r.sender_chat.title
            reply["text"] = self.get_reply_text(r)

        return {
            "text": text,
            "media": media,
            "entities": entities,
            "author": author,
            "reply": reply,
        }

    def get_full_name(self, user) -> str:
        name = user.first_name or ""
        if user.last_name:
            name += " " + user.last_name
        return name

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
            return (reply.sticker.emoji or "") + " Sticker"
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
