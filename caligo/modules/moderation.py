import asyncio
from typing import ClassVar, Optional

from pyrogram.enums import ChatMembersFilter, ChatType
from pyrogram.types import ChatMember

from caligo import command, module
from caligo.util import time as util_time


class Moderation(module.Module):
    name: ClassVar[str] = "Moderation"

    @command.desc("Mention everyone in this group with flexible targeting options")
    @command.usage("[comment?] [--admins] [--members]", optional=True)
    async def cmd_tagall(
        self,
        ctx: command.Context,
        *,
        tag: str = "everyone",
        user_filter: Optional[ChatMembersFilter] = None,
    ) -> Optional[str]:
        comment = ctx.input.strip()

        if ctx.msg.chat.type == ChatType.PRIVATE:
            return "__This command can only be used in groups.__"

        # Parse flags to determine target audience
        has_admins_flag = "admins" in ctx.flags
        has_members_flag = "members" in ctx.flags

        # Determine filter and tag based on flags
        if has_admins_flag:
            target_filter = ChatMembersFilter.ADMINISTRATORS
            tag = "admin"
        elif has_members_flag:
            target_filter = ChatMembersFilter.SEARCH
            tag = "everyone"
        else:
            # No flags = everyone (default behavior)
            target_filter = user_filter or ChatMembersFilter.SEARCH
            if tag == "everyone":
                tag = "everyone"

        # Clean comment from flags if they exist
        if comment:
            # Remove flag arguments from comment
            comment_parts = []
            tokens = comment.split()

            for token in tokens:
                if not (
                    token.startswith("-") and token.lstrip("-") in ["admins", "members"]
                ):
                    comment_parts.append(token)

            comment = " ".join(comment_parts).strip()

        # Build mention text
        mention_text = f"@{tag}"
        if comment:
            mention_text += " " + comment

        mention_slots = 4096 - len(mention_text)

        chat = ctx.msg.chat.id
        member: ChatMember
        async for member in self.bot.client.get_chat_members(
            chat, filter=target_filter
        ):  # type: ignore
            mention_text += f"[\u200c](tg://user?id={member.user.id})"

            mention_slots -= 1
            if mention_slots == 0:
                break

        await ctx.respond(mention_text, mode="repost")

    @command.desc("Mention all admins in a group (**DO NOT ABUSE**)")
    @command.usage("[comment?]", optional=True)
    async def cmd_admin(self, ctx: command.Context) -> Optional[str]:
        return await self.cmd_tagall(
            ctx, tag="admin", user_filter=ChatMembersFilter.ADMINISTRATORS
        )

    @command.desc("Reply to a message, mark as start until your purge command.")
    @command.usage("purge", reply=True)
    async def cmd_purge(self, ctx: command.Context) -> Optional[str]:
        if not ctx.msg.reply_to_message:
            return "__Reply to a message.__"

        await ctx.respond("__Purging...__")

        start_us = util_time.usec()

        start, end = ctx.msg.reply_to_message.id, ctx.msg.id
        messages_id = []
        purged = 0

        for message_id in range(start, end):
            messages_id.append(message_id)
            if len(messages_id) == 100:
                purged += await ctx.bot.client.delete_messages(
                    chat_id=ctx.msg.chat.id, message_ids=messages_id
                )
                messages_id = []

        if messages_id:
            purged += await ctx.bot.client.delete_messages(
                chat_id=ctx.msg.chat.id, message_ids=messages_id, revoke=True
            )

        elapsed_us = util_time.usec() - start_us
        msg = "message" if purged == 1 else "messages"

        await ctx.respond(
            f"__Purged {purged} {msg} in {util_time.format_duration_us(elapsed_us)}...__",
            mode="repost",
            delete_after=1.5,
        )

    @command.desc("Delete the replied message.")
    @command.usage("del", reply=True)
    async def cmd_del(self, ctx: command.Context) -> Optional[str]:
        if not ctx.msg.reply_to_message:
            return "__Reply to a message.__"

        await asyncio.gather(
            ctx.msg.reply_to_message.delete(), ctx.msg.delete(), return_exceptions=True
        )
