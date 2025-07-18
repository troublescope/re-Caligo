import json

from aiopath import AsyncPath
from PIL import Image

from .async_helpers import run_sync
from .system import run_command

MAX_VIDEO_SIZE = 10485760
MAX_SIZE = 512
CACHE_PATH = "caligo/.cache/stickers"


async def resize_media_sticker(media: AsyncPath, video: bool) -> AsyncPath:
    if video:
        stdout, _, __ = await run_command(
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(media),
        )
        metadata = json.loads(stdout)
        width = round(metadata["streams"][0].get("width", 512))
        height = round(metadata["streams"][0].get("height", 512))

        if height == width:
            height, width = 512, 512
        elif height > width:
            height, width = 512, -1
        elif width > height:
            height, width = -1, 512

        resized_video = f"{CACHE_PATH}/{media.stem}.webm"
        await run_command(
            "ffmpeg",
            "-i",
            str(media),
            "-ss",
            "00:00:00",
            "-to",
            "00:00:03",
            "-map",
            "0:v",
            "-b",
            "256k",
            "-fs",
            "262144",
            "-c:v",
            "libvpx-vp9",
            "-vf",
            f"scale={width}:{height},fps=30",
            resized_video,
            "-y",
        )
        await media.unlink()
        return AsyncPath(resized_video)

    image: Image.Image = await run_sync(Image.open, str(media))
    scale = MAX_SIZE / max(image.width, image.height)
    image = await run_sync(
        image.resize,
        (int(image.width * scale), int(image.height * scale)),
        Image.LANCZOS,
    )

    resized_photo = f"{CACHE_PATH}/sticker.png"
    await run_sync(image.save, resized_photo, "PNG")

    await media.unlink()
    return AsyncPath(resized_photo)
