import base64
import json
import mimetypes
import os

import openai
from telethon.tl.custom import Message
from telethon.tl.types import User, Channel

from llm import llm_client
from utils.helpers import get_request_data, resend_processing_status


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
    response = await llm_client.chat.completions.create(
        model=os.environ["COMPLETIONS_MODEL"],
        messages=[request_data])
    media_description = response.choices[0].message.content
    os.remove(media)
    return media_description

async def send_data(update,
                    context,
                    data,
                    message_text: str,
                    message=None,
                    for_summary=False,
                    tries_left: int|None = None,
                    cur_status: int|None = None,
                    msg_count: int|None = None):
    while True:
        try:
            request_data = get_request_data(data, message_text=message_text)
            response = await llm_client.chat.completions.create(
                model=os.environ["COMPLETIONS_MODEL"],
                messages=request_data)
            new_data = response.choices[0].message.content
            if for_summary:
                new_data = json.loads(new_data)
            else:
                new_data = [new_data]
            cur_status += len(data)
            if message:
                await resend_processing_status(update.effective_user.id, context.bot, message, cur_status, msg_count)
            return new_data
        except json.decoder.JSONDecodeError:
            tries_left -= 1
            if tries_left == 0:
                text = "Problem with server. Try again later."
            else:
                text = "Error occurred. Retrying..."
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
            if tries_left == 0:
                raise
        except openai.BadRequestError as e:
            mid = len(data) // 2
            left_data = await send_data(update, context, data[:mid], message_text, message, tries_left, cur_status, msg_count)
            right_data = await send_data(update, context, data[mid:], message_text, message, tries_left, cur_status, msg_count)
            return left_data + right_data