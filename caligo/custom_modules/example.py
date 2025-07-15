import asyncio
import io
import sys
from typing import BinaryIO, ClassVar

if sys.version_info >= (3, 10):
    from aiopath import AsyncPurePath as PosixPath
else:
    from aiopath import PureAsyncPosixPath as PosixPath

from pyrogram.types import Message

from caligo import command, listener, module
from caligo.core import database


class ExampleModule(module.Module):
    name: ClassVar[str] = "Example"
    disabled: ClassVar[bool] = True
    helpable: ClassVar[bool] = False

    db: database.AsyncCollection

    async def on_load(self) -> None:
        self.db = self.bot.db.get_collection("example")

    @listener.priority(50)  # The less the number, the higher the priority
    async def on_message(self, message: Message) -> None:
        self.log.info(f"Received message: {message.text}")
        await self.db.update_one(
            {"_id": message.id}, {"$set": {"text": message.text}}, upsert=True
        )

    async def on_message_delete(self, message: Message) -> None:
        self.log.info(f"Message deleted: {message.text}")
        await self.db.delete_one({"_id": message.id})

    async def on_chat_action(self, message: Message) -> None:
        if message.new_chat_members:
            for new_member in message.new_chat_members:
                self.log.info("New member joined: %s", new_member.first_name)
        else:
            left_member = message.left_chat_member
            self.log.info("A member just left chat: %s", left_member.first_name)

    @command.desc("Test command with flags support")
    @command.usage("[text] [--verbose] [--count=N] [--format=json|text] [-q]")
    async def cmd_test(self, ctx: command.Context) -> str:
        # Parse flags
        verbose = ctx.flags.get("verbose", False)
        quiet = ctx.flags.get("q", False)
        count = int(ctx.flags.get("count", 1))
        output_format = ctx.flags.get("format", "text")

        if verbose and not quiet:
            await ctx.respond("Processing with verbose output enabled...")
        elif not quiet:
            await ctx.respond("Processing...")

        await asyncio.sleep(1)

        # Process input text
        if ctx.input:
            text = ctx.input
        else:
            text = "It works!"

        # Generate response based on count
        if count > 1:
            text = f"{text} " * count

        # Format output
        if output_format == "json":
            import json

            response = json.dumps(
                {
                    "text": text,
                    "flags": dict(ctx.flags),
                    "args": list(ctx.args),
                    "verbose": verbose,
                    "count": count,
                },
                indent=2,
            )
        else:
            response = text

        if verbose and not quiet:
            response += f"\n\nFlags used: {dict(ctx.flags)}"
            response += f"\nArgs: {list(ctx.args)}"

        return response

    async def get_cat(self) -> BinaryIO:
        # Get the link to a random cat picture
        async with self.bot.http.get("https://aws.random.cat/meow") as resp:
            # Read and parse the response as JSON
            json = await resp.json()
            # Get the "file" field from the parsed JSON object
            cat_url = json["file"]

        # Get the actual cat picture
        async with self.bot.http.get(cat_url) as resp:
            # Get the data as a byte array (bytes object)
            cat_data = await resp.read()

        # Construct a byte stream from the data.
        # This is necessary because the bytes object is immutable, but we need to add a "name" attribute to set the
        # filename. This facilitates the setting of said attribute without altering behavior.
        cat_stream = io.BytesIO(cat_data)

        # Set the name of the cat picture before sending.
        # This is necessary for Pyrogram to detect the file type and send it as a photo/GIF rather than just a plain
        # unnamed file that doesn't render as media in clients.
        # We abuse aiopath to extract the filename section here for convenience, since URLs are *mostly* POSIX paths
        # with the exception of the protocol part, which we don't care about here.
        cat_stream.name = PosixPath(cat_url).name

        return cat_stream

    @command.desc("Fetch a random cat picture")
    @command.usage("[--gif] [--caption=text] [--count=N] [-q|--quiet]")
    async def cmd_cat(self, ctx: command.Context) -> None:
        # Parse flags
        gif_only = ctx.flags.get("gif", False)
        quiet = ctx.flags.get("q", False) or ctx.flags.get("quiet", False)
        count = int(ctx.flags.get("count", 1))
        custom_caption = ctx.flags.get("caption")

        # Validate count
        if count > 5:
            await ctx.respond("Maximum 5 cats at once!")
            return

        if count < 1:
            count = 1

        if not quiet:
            if count == 1:
                await ctx.respond("Fetching cat...")
            else:
                await ctx.respond(f"Fetching {count} cats...")

        # Send multiple cats if requested
        for i in range(count):
            cat_stream = await self.get_cat()

            # Check if it's a GIF when gif_only flag is set
            if gif_only and not cat_stream.name.lower().endswith(".gif"):
                # Try to get another cat if this one isn't a GIF
                continue

            caption = custom_caption or (f"Cat {i+1}/{count}" if count > 1 else None)

            await self.bot.client.send_animation(
                ctx.chat.id,
                cat_stream,
                caption=caption,
                message_thread_id=ctx.msg.message_thread_id,
            )

            # Small delay between multiple cats
            if i < count - 1:
                await asyncio.sleep(0.5)

    @command.desc("Database operations with flags")
    @command.usage("[--list] [--clear] [--count] [--filter=text] [--export]")
    async def cmd_db(self, ctx: command.Context) -> None:
        # Parse flags
        list_entries = ctx.flags.get("list", False)
        clear_db = ctx.flags.get("clear", False)
        count_entries = ctx.flags.get("count", False)
        filter_text = ctx.flags.get("filter")
        export_data = ctx.flags.get("export", False)

        # Count entries
        if count_entries:
            query = {}
            if filter_text:
                query = {"text": {"$regex": filter_text, "$options": "i"}}

            total = await self.db.count_documents(query)
            filter_msg = f" (filtered by: {filter_text})" if filter_text else ""
            await ctx.respond(f"Database contains {total} entries{filter_msg}")
            return

        # List entries
        if list_entries:
            query = {}
            if filter_text:
                query = {"text": {"$regex": filter_text, "$options": "i"}}

            entries = await self.db.find(query).limit(10).to_list(length=None)

            if not entries:
                await ctx.respond("No entries found")
                return

            response = "Recent entries:\n"
            for entry in entries:
                response += f"• {entry.get('text', 'N/A')[:50]}...\n"

            await ctx.respond(response)
            return

        # Export data
        if export_data:
            query = {}
            if filter_text:
                query = {"text": {"$regex": filter_text, "$options": "i"}}

            entries = await self.db.find(query).to_list(length=None)

            if not entries:
                await ctx.respond("No data to export")
                return

            import json

            export_data = json.dumps(entries, indent=2, default=str)

            # Create file-like object
            export_file = io.StringIO(export_data)
            export_file.name = "database_export.json"

            await self.bot.client.send_document(
                ctx.chat.id,
                export_file,
                caption="Database export",
                message_thread_id=ctx.msg.message_thread_id,
            )
            return

        # Clear database
        if clear_db:
            result = await self.db.delete_many({})
            await ctx.respond(f"Cleared {result.deleted_count} entries from database")
            return

        # Default: show help
        await ctx.respond(
            "Available flags:\n"
            "• --list: List recent entries\n"
            "• --count: Count total entries\n"
            "• --clear: Clear all entries\n"
            "• --filter=text: Filter by text content\n"
            "• --export: Export data as JSON"
        )
