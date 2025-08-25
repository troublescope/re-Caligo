import base64
import binascii
import random
import re
import sre_constants
import unicodedata
from typing import ClassVar, Optional, Tuple

from pyrogram import filters

from caligo import command, listener, module

DELIMITERS = ("/", ":", "|", "_")


class Text(module.Module):
    name: ClassVar[str] = "Text"

    def _get_text_input(self, ctx: command.Context) -> Optional[str]:
        """Helper method to get text from input or reply message."""
        text = ctx.input
        if not text and ctx.msg.reply_to_message:
            text = ctx.msg.reply_to_message.text
        return text

    @command.desc("Unicode character from hex codepoint")
    @command.usage("[hexadecimal Unicode codepoint]")
    async def cmd_uni(self, ctx: command.Context) -> str:
        codepoint = ctx.input.strip()
        if not codepoint:
            return "__Please provide a hexadecimal Unicode codepoint.__"

        # Remove common prefixes if present
        if codepoint.lower().startswith(("0x", "u+", "\\u")):
            codepoint = codepoint[2:]

        try:
            code_int = int(codepoint, 16)
            if code_int > 0x10FFFF:  # Valid Unicode range
                return "__Input is out of Unicode's valid range of__ `0x00000` __to__ `0x10FFFF`__.__"
            return chr(code_int)
        except ValueError:
            return (
                "__Invalid hexadecimal input. Please provide a valid hex codepoint.__"
            )

    @command.desc("Apply a sarcasm/mocking filter to the given text")
    @command.usage("[text to filter]", reply=True)
    async def cmd_mock(self, ctx: command.Context) -> str:
        text = self._get_text_input(ctx)
        if not text:
            return "__Give me a text or reply to a message.__"

        # More efficient approach using list comprehension
        return "".join(
            ch.upper() if random.choice((True, False)) else ch.lower() for ch in text
        )

    @command.desc("Dissect a string into named Unicode codepoints")
    @command.usage("[text to dissect]", reply=True)
    async def cmd_charinfo(self, ctx: command.Context) -> str:
        text = self._get_text_input(ctx)
        if not text:
            return "__Give me a text or reply to a message.__"

        if len(text) > 100:  # Prevent spam with very long texts
            return "__Text too long. Please provide text with 100 characters or less.__"

        chars = []
        for char in text:
            preview = char not in "`\n\r\t"  # Don't preview problematic characters
            try:
                name = unicodedata.name(char)
            except ValueError:
                name = "UNNAMED CONTROL CHARACTER"
                preview = False

            line = f"`U+{ord(char):04X}` {name}"
            if preview:
                line += f" `{char}`"
            chars.append(line)

        return "\n".join(chars)

    @command.desc("Replace the spaces in a string with clap emoji")
    @command.usage("[text to filter, or reply]", reply=True)
    async def cmd_clap(self, ctx: command.Context) -> str:
        text = self._get_text_input(ctx)
        if not text:
            return "__Give me a text or reply to a message.__"

        # Join with clap emoji, preserving line breaks
        return "\n".join("👏".join(line.split()) for line in text.split("\n"))

    @command.desc("Encode text into Base64")
    @command.alias("b64encode", "b64e")
    @command.usage("[text to encode, or reply]", reply=True)
    async def cmd_base64encode(self, ctx: command.Context) -> str:
        text = self._get_text_input(ctx)
        if not text:
            return "__Give me a text or reply to a message.__"

        try:
            encoded = base64.b64encode(text.encode("utf-8")).decode()
            # Format long base64 strings nicely
            if len(encoded) > 64:
                return f"```\n{encoded}\n```"
            return f"`{encoded}`"
        except Exception as e:
            return f"⚠️ Encoding failed: {e}"

    @command.desc("Decode Base64 data")
    @command.alias("b64decode", "b64d")
    @command.usage("[base64 text to decode, or reply]", reply=True)
    async def cmd_base64decode(self, ctx: command.Context) -> str:
        text = self._get_text_input(ctx)
        if not text:
            return "__Give me a text or reply to a message.__"

        # Clean up the input (remove whitespace and common formatting)
        text = re.sub(r"\s", "", text.strip("`"))

        try:
            decoded = base64.b64decode(text).decode("utf-8", "replace")
            return decoded
        except (binascii.Error, ValueError) as e:
            return f"⚠️ Invalid Base64 data: {e}"

    def _parse_sed(self, expr: str) -> Optional[Tuple[str, str, str]]:
        """Parse sed-like expression and return pattern, replacement, and flags."""
        if len(expr) < 4 or expr[0] != "s" or expr[1] not in DELIMITERS:
            return None

        delim = expr[1]
        parts = expr[2:].split(delim)

        if len(parts) < 2:
            return None

        pattern = parts[0]
        replacement = parts[1] if len(parts) > 1 else ""
        flags = parts[2].lower() if len(parts) > 2 else ""

        return pattern, replacement, flags

    @listener.priority(101)
    @listener.filters(filters.regex(r"^s([/:\|_]).*?\1.*") & filters.me)
    async def on_message(self, msg):
        """Handle sed-like replacements on replied messages."""
        if not msg.reply_to_message:
            return

        parsed = self._parse_sed(msg.content)
        if not parsed:
            return

        pattern, repl_with, flags = parsed
        target = msg.reply_to_message.content
        if not target:
            return

        # Build regex flags
        regex_flags = 0
        if "i" in flags:
            regex_flags |= re.I
        if "m" in flags:
            regex_flags |= re.M
        if "s" in flags:
            regex_flags |= re.S

        try:
            if "g" in flags:
                result = re.sub(pattern, repl_with, target, flags=regex_flags)
            else:
                result = re.sub(pattern, repl_with, target, count=1, flags=regex_flags)
        except (sre_constants.error, re.error):
            return  # Silently fail on invalid regex

        # Only edit if result is different and within reasonable length
        if result != target and result and len(result) < 4096:
            await msg.edit(
                f"<i>Did you mean?</i>\n<blockquote><code>{result}</code></blockquote>"
            )

    @command.desc("Get length and character count of text")
    @command.usage("[text to analyze]", reply=True)
    async def cmd_length(self, ctx: command.Context) -> str:
        """Analyze text length and character counts."""
        text = self._get_text_input(ctx)
        if not text:
            return "__Give me a text or reply to a message.__"

        char_count = len(text)
        byte_count = len(text.encode("utf-8"))
        word_count = len(text.split())
        line_count = text.count("\n") + 1

        return (
            f"**Text Analysis:**\n"
            f"Characters: `{char_count}`\n"
            f"Bytes (UTF-8): `{byte_count}`\n"
            f"Words: `{word_count}`\n"
            f"Lines: `{line_count}`"
        )

    @command.desc("Reverse the given text")
    @command.usage("[text to reverse]", reply=True)
    async def cmd_reverse(self, ctx: command.Context) -> str:
        """Reverse text character by character."""
        text = self._get_text_input(ctx)
        if not text:
            return "__Give me a text or reply to a message.__"

        return text[::-1]
