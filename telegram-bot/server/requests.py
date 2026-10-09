import asyncio
import base64
import mimetypes
import os
from uuid import uuid4

from pgvector import Vector
from telethon.tl.custom import Message, Dialog
from telethon.tl.types import User, Channel, ForumTopic, MessageActionChatJoinedByLink

from database import cur, connection
from server import get_host, active_requests, available_hosts
from utils.classes import Request
from utils.helpers import send_message


def image_to_data_url(path: str | os.PathLike) -> str:
    mime_type, _ = mimetypes.guess_file_type(path)
    with open(path, "rb") as file:
        encoded = base64.b64encode(file.read()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"

async def send_request(user_id, data, operation, search_text=None):
    while True:
        host = await get_host(user_id)
        if host is None:
            return None
        # if host returns blank results for 5 times
        # then user is notified and websocket is closed
        for _ in range(5):
            future = asyncio.get_event_loop().create_future()
            request_id = uuid4().int
            data = {
                "user_id": user_id,
                "request_id": request_id,
                "operation": operation,
                "data": data,
                "search_text": search_text
            }
            active_requests[request_id] = Request(user_id, host, future)
            await host.websocket.send_json(data)

            result = await future
            if result is None:
                continue
            return result
        else:
            await host.websocket.close()
            await send_message(host.user.user_id, "Your machine returns blank responses, please make sure that everything "
                                                  "is functioning again and reconnect.")
            del available_hosts[host.user.user_id]

async def request_media_description(user_id, message: Message):
    media = await message.download_media("media")
    image_data = image_to_data_url(media)

    result = await send_request(user_id, image_data, "image")

    #check this
    print(result)
    os.remove(media)
    return result

async def request_embedding(dialog: tuple[Dialog, ForumTopic], message: Message, user_id: int, media_description: str | None, update: bool = False) -> Vector:
    text = "Text: " + ("None" if message.text is None else message.text)
    media = "Media: " + ("None" if media_description is None else media_description)
    full_text = text + "\n" + media

    embedding = await send_request(user_id, full_text, "embedding")
    if embedding is None:
        pass

    if update:
        if isinstance(dialog[0].entity, Channel):
            topic_id = dialog[1].id if dialog[1] else 0
            cur.execute("""
            UPDATE public_message
            SET embedding = %s
            WHERE message_id = %s and
                  channel_id = %s and
                  topic_id = %s
            """, (Vector(embedding), message.id, dialog[0].id, topic_id))
        else:
            cur.execute("""
            UPDATE private_message
            SET embedding = %s
            WHERE message_id = %s and
                  dialog_id = %s and
                  user_id = %s
            """, (Vector(embedding), message.id, dialog[0].id, user_id))
        connection.commit()
    return Vector(embedding)