import base64
import json
import mimetypes
import os

import openai
from telethon.tl.custom import Message
from telethon.tl.types import User, Channel

from llm import llm_client
from utils.helpers import get_request_data


async def translate_image(image_data):
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
                    "url": image_data
                }
            }
        ]
    }
    response = await llm_client.chat.completions.create(
        model=os.environ["COMPLETIONS_MODEL"],
        messages=[request_data])
    media_description = response.choices[0].message.content
    return media_description

async def send_data(data,
                    message_text: str,
                    for_summary=False,
                    tries_left: int|None = None):
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
            return new_data
        except json.decoder.JSONDecodeError:
            tries_left -= 1
            if tries_left == 0:
                raise
        except openai.BadRequestError:
            mid = len(data) // 2
            left_data = await send_data(data[:mid], message_text, for_summary, tries_left)
            right_data = await send_data(data[mid:], message_text, for_summary, tries_left)
            return left_data + right_data