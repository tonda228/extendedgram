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
                last_update = monotonic()
                with suppress(TimeoutError, TelegramError):
                    if await asyncio.wait_for(resend_processing_status(
                            request.user_id, progress_message, completed, total, label=label), timeout=2):
                        last_status = status
        finally:
            progress_task.cancel()
            with suppress(asyncio.CancelledError):
                await progress_task


async def send_request(user_id: int, data: str | list[str] | list[dict[str, Any]], operation: str, search_text: str | None=None,
                       *, embedding_model: str | None = None) -> Any:
    is_model_request = operation in ("search", "summarize")
    response_timeout = MODEL_REQUEST_TIMEOUT if operation == "embedding" and isinstance(data, list) else REQUEST_TIMEOUT
    model = embedding_model or (os.getenv("EMBEDDING_MODEL") if operation == "embedding" else None)
    if operation == "embedding" and not model:
        return None
    host = await get_host(user_id, embedding_model=model) if model else await get_host(user_id)
    if host is None:
        return None

    progress_message = None
    try:
        host.requests_count += 1
        update_host_priorities(host)
        if is_model_request:
            progress_message = await send_message(user_id, "Waiting for the model...")
        # Wait for earlier work before starting this request's response timeout.
        async with host.request_lock:
            if host.disconnected:
                return None
            future = asyncio.get_running_loop().create_future()
            request_id = uuid4().int
            payload = {
                "user_id": user_id, "request_id": request_id,
                "operation": operation, "data": data, "search_text": search_text,
            }
            if operation == "embedding":
                payload["embedding_model"] = model
            request = Request(user_id=user_id, host=host, future=future,
                              progress_queue=asyncio.Queue(maxsize=1) if is_model_request else None)
            active_requests[request_id] = request
            send_task = None
            try:
                send = asyncio.wait_for(host.websocket.send_json(payload), REQUEST_TIMEOUT)
                if is_model_request:
                    await send
                    return await wait_for_model(request, progress_message)
                send_task = asyncio.create_task(send)
                await asyncio.shield(send_task)
                return await asyncio.wait_for(asyncio.shield(future), response_timeout)
            except asyncio.CancelledError:
                # Let a short in-flight request finish before releasing the lock.
                # Stopping preloading should not disconnect an otherwise healthy host.
                disconnect = True
                if send_task is not None and not send_task.cancelled():
                    try:
                        await asyncio.shield(send_task)
                        await asyncio.wait_for(asyncio.shield(future), REQUEST_TIMEOUT)
                        disconnect = False
                    except (asyncio.CancelledError, TimeoutError, WebSocketDisconnect, OSError, RuntimeError):
                        pass
                if disconnect:
                    if send_task is not None and not send_task.done():
                        send_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await send_task
                    await disconnect_host(host)
                    await finish_requests(host)
                    await close_websocket(host, notify=False)
                raise
            except (TimeoutError, WebSocketDisconnect, OSError, RuntimeError):
                await disconnect_host(host)
                await finish_requests(host)
                await close_websocket(host)
                return None
            finally:
                active_requests.pop(request_id, None)
                if not future.done():
                    future.cancel()
    finally:
        host.requests_count -= 1
        update_host_priorities(host)
        if progress_message is not None:
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


async def request_embeddings(user_id: int, texts: list[str], embedding_model: str) -> list[Vector] | None:
    if not texts:
        return []
    result = await send_request(user_id, texts, "embedding", embedding_model=embedding_model)
    if not isinstance(result, list) or len(result) != len(texts):
        return None
    try:
        vectors = [Vector(values) for values in result]
        if any(vector.dimensions() != 1024 or not all(math.isfinite(value) for value in vector.to_list())
               for vector in vectors):
            return None
        return vectors
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
