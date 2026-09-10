import base64
import mimetypes
import os

from telethon.tl.custom import Message
from telethon.tl.types import User, Channel

from llm import COMPLETIONS_MODEL, completions_client


def image_to_data_url(path: str | os.PathLike) -> str:
    mime_type, _ = mimetypes.guess_file_type(path)
    with open(path, "rb") as file:
        encoded = base64.b64encode(file.read()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"

async def translate_image(message: Message):
    media = await message.download_media("media")
    request_data = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "Give very concise one sentence description of this image"
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": image_to_data_url(media)
                }
            }
        ]
    }
    response = await completions_client.chat.completions.create(
        model=COMPLETIONS_MODEL,
        messages=[request_data])
    media_description = response.choices[0].message.content
    os.remove(media)
    return media_description