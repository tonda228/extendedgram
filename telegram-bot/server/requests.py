from typing import Any

import asyncio
from contextlib import suppress
from time import monotonic
import base64
import mimetypes
import os
import math
from uuid import uuid4

from pgvector import Vector
from starlette.websockets import WebSocketDisconnect
from telegram import Message as TelegramMessage
from telegram.error import TelegramError
from telethon.tl.custom import Message, Dialog
from telethon.tl.types import User, Channel, ForumTopic, MessageActionChatJoinedByLink

from database import cur, connection
from server import get_host, active_requests, update_host_priorities, disconnect_host, close_websocket, finish_requests
from utils.classes import Request
from utils.helpers import send_message, resend_processing_status

REQUEST_TIMEOUT = 120
MODEL_REQUEST_TIMEOUT = 1800

def image_to_data_url(path: str | os.PathLike) -> str:
    mime_type, _ = mimetypes.guess_file_type(path)
    with open(path, "rb") as file:
        encoded = base64.b64encode(file.read()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"

async def wait_for_model(request: Request, progress_message: TelegramMessage | None) -> Any:
    progress_queue = request.progress_queue
    if progress_queue is None:
        raise ValueError("Model requests require a progress queue")
    deadline = monotonic() + MODEL_REQUEST_TIMEOUT
    last_update = 0.0
    last_status = None
    while True:
        timeout = min(REQUEST_TIMEOUT, deadline - monotonic())
        if timeout <= 0:
            raise TimeoutError
        progress_task = asyncio.create_task(progress_queue.get())
        try:
            done, _ = await asyncio.wait(
                (request.future, progress_task), timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED)
            if request.future in done:
                return request.future.result()
            if progress_task not in done:
                raise TimeoutError
            completed, total, stage = progress_task.result()
            status = (stage, completed * 100 // total)
            if status != last_status and monotonic() - last_update >= 3:
                label = "Combining results..." if stage == "merging" else "Processing messages..."
                if await resend_processing_status(request.user_id, progress_message, completed, total, label=label):
                    last_update = monotonic()
                    last_status = status
        finally:
            progress_task.cancel()
            with suppress(asyncio.CancelledError):
                await progress_task


async def send_request(user_id: int, data: str | list[dict[str, Any]], operation: str, search_text: str | None=None,
                       *, embedding_model: str | None = None) -> Any:
    is_model_request = operation in ("search", "summarize")
    progress_message = None
    try:
        if is_model_request:
            progress_message = await send_message(user_id, "Waiting for the model...")
        while True:
            model = embedding_model or (os.getenv("EMBEDDING_MODEL") if operation == "embedding" else None)
            if operation == "embedding" and not model:
                return None
            host = await get_host(user_id, embedding_model=model) if model else await get_host(user_id)
            if host is None:
                return None
            failed = False
            try:
                host.requests_count += 1
                update_host_priorities(host)
                # The host reads requests sequentially. Waiting for the lock must
                # not consume the response timeout of a request still in the queue.
                async with host.request_lock:
                    if host.disconnected:
                        return None
                    for _ in range(1 if is_model_request else 5):
                        future = asyncio.get_running_loop().create_future()
                        request_id = uuid4().int
                        payload = {
                            "user_id": user_id, "request_id": request_id,
                            "operation": operation, "data": data,
                            "search_text": search_text,
                        }
                        if operation == "embedding":
                            payload["embedding_model"] = model
                        request = Request(user_id=user_id, host=host, future=future,
                                          progress_queue=asyncio.Queue(maxsize=1) if is_model_request else None)
                        active_requests[request_id] = request
                        try:
                            await asyncio.wait_for(host.websocket.send_json(payload), REQUEST_TIMEOUT)
                            if is_model_request:
                                result = await wait_for_model(request, progress_message)
                            else:
                                result = await asyncio.wait_for(future, REQUEST_TIMEOUT)
                        except TimeoutError:
                            # Do not enqueue another copy behind a model request
                            # that may still be running on the host.
                            failed = True
                            break
                        except asyncio.CancelledError:
                            # This connection still has the cancelled work queued.
                            await disconnect_host(host)
                            await finish_requests(host)
                            await close_websocket(host, notify=False)
                            raise
                        except (WebSocketDisconnect, OSError, RuntimeError):
                            failed = True
                            break
                        finally:
                            active_requests.pop(request_id, None)
                            if not future.done():
                                future.cancel()
                        if result is not None:
                            return result
                        if is_model_request:
                            # A model/API error does not mean the machine is offline.
                            return None
                    else:
                        failed = True
                    if failed:
                        await disconnect_host(host)
                        await finish_requests(host)
                        await close_websocket(host)
                # Fail once and let the feature show a single useful error.
                return None
            finally:
                host.requests_count -= 1
                update_host_priorities(host)
    finally:
        if progress_message is not None:
            # This is the dedicated progress message, never the current menu.
            with suppress(TelegramError):
                await progress_message.delete()


async def request_media_description(user_id: int, message: Message) -> str | None:
    media = await message.download_media("media")
    if media is None:
        return None
    try:
        image_data = image_to_data_url(media)
        return await send_request(user_id, image_data, "image")
    finally:
        if os.path.exists(media):
            os.remove(media)

async def request_embedding(user_id: int, text: str, embedding_model: str | None = None) -> Vector|None:
    embedding = (await send_request(user_id, text, "embedding", embedding_model=embedding_model)
                 if embedding_model else await send_request(user_id, text, "embedding"))
    if embedding is None:
        return None
    try:
        vector = Vector(embedding)
        if vector.dimensions() != 1024 or not all(math.isfinite(value) for value in vector.to_list()):
            return None
        return vector
    except (ValueError, TypeError, OverflowError):
        return None

async def request_message_embedding(dialog: tuple[Dialog, ForumTopic | None], message: Message, user_id: int,
                                    media_description: str | None, update: bool = False,
                                    embedding_model: str | None = None) -> Vector|None:
    if update and embedding_model and embedding_model != os.getenv("EMBEDDING_MODEL"):
        raise ValueError("Use model-specific storage for non-default embeddings")
    text = "Text: " + ("None" if message.text is None else message.text)
    media = "Media: " + ("None" if media_description is None else media_description)
    full_text = text + "\n" + media

    embedding = (await request_embedding(user_id, full_text, embedding_model)
                 if embedding_model else await request_embedding(user_id, full_text))
    if embedding is None:
        return None

    if update:
        if isinstance(dialog[0].entity, Channel):
            topic_id = dialog[1].id if dialog[1] else 0
            cur.execute("""
            UPDATE public_message
            SET embedding = %s
            WHERE message_id = %s and
                  channel_id = %s and
                  topic_id = %s
            """, (embedding, message.id, dialog[0].entity.id if isinstance(dialog[0], Dialog) else dialog[0].id, topic_id))
        else:
            topic_id = dialog[1].id if dialog[1] else 0
            cur.execute("""
            UPDATE private_message
            SET embedding = %s
            WHERE message_id = %s and
                  dialog_id = %s and
                  user_id = %s and
                  topic_id = %s
            """, (embedding, message.id, dialog[0].id, user_id, topic_id))
        connection.commit()
    return embedding
