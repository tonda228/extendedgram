from psycopg.rows import class_row
from heapq import heapify, heappush

import hashlib
import json
import os
import secrets
from time import monotonic

from telethon import TelegramClient
from telethon.sessions import StringSession

from bot.commands.menu import menu
from database import cur, connection
from fastapi import WebSocket, WebSocketDisconnect, HTTPException
from fastapi.applications import AppType
from fastapi.security import HTTPBearer
from telegram.error import TelegramError

from database.groups import get_app_user_groups
from database.users import store_telegram_user
from features import event_handlers
from features.preloading import reset_idle_timer
from server import available_hosts, active_requests, available_group_hosts, disconnect_host, close_websocket, \
    finish_requests
from utils.classes import Host, UserState, AppUser, AppUserDBObject, AppUserPreloading
from utils.embedding_models import parse_embedding_models
from utils.helpers import get_user_name, send_message
from utils.security import decrypt_string
from utils.state import user_info, user_preloading
from utils.session import record_activity
from pydantic import BaseModel, SecretStr

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]

bearer = HTTPBearer()

def create_api_handlers(app: AppType) -> None:
    app.post("/pair")(check_pairing_code)
    app.websocket("/ws/{host_id}")(register_new_host)

def authenticate_host(host_id: int, token: str) -> AppUserDBObject | None:
    with connection.cursor(row_factory=class_row(AppUserDBObject)) as db_cur:
        db_cur.execute("""
        SELECT *
        FROM app_user
        WHERE user_id = %s AND is_host = TRUE
        """, (host_id,))
        host = db_cur.fetchone()
    if host is None:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    if host.token_hash != token_hash:
        return None
    return host

class PairingRequest(BaseModel):
    user_id: int
    code: str
    password: SecretStr


async def check_pairing_code(info: PairingRequest) -> dict[str, str]:
    user_id = info.user_id
    pairing_code = info.code

    if user_id not in user_info or not user_info[user_id].is_host:
        raise HTTPException(status_code=404, detail="Host not found")
    app_user = user_info[user_id]
    hashed = app_user.pairing_code_hash
    if hashed is None or app_user.pairing_code_expires_at <= monotonic():
        raise HTTPException(status_code=404, detail="No active pairing session")
    if (not app_user.password_hash
            or not secrets.compare_digest(hashed, hashlib.sha256(pairing_code.encode()).hexdigest())
            or not secrets.compare_digest(app_user.password_hash, hashlib.sha256(info.password.get_secret_value().encode()).hexdigest())):
        raise HTTPException(status_code=403, detail="Invalid pairing code or bot password")

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    try:
        cur.execute("""
        UPDATE app_user
        SET token_hash = %s
        WHERE user_id = %s AND is_host = TRUE
        """, (token_hash, user_id))
        if cur.rowcount != 1:
            raise HTTPException(status_code=404, detail="Host not found")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    app_user.pairing_code_hash = None
    app_user.pairing_code_expires_at = 0.0
    app_user.token_hash = token_hash
    return {"token": token}


async def register_new_host(websocket: WebSocket, host_id: int) -> None:
    authorization = websocket.headers.get("authorization")
    password = websocket.headers.get("password")
    advertised_models = websocket.headers.get("embedding-models")
    try:
        embedding_models = parse_embedding_models(json.loads(advertised_models) if advertised_models is not None
                                                   else [websocket.headers.get("embedding-model")])
    except ValueError:
        await websocket.close(code=1008, reason="A valid Embedding-Models or Embedding-Model header is required")
        return
    if authorization is None or password is None:
        await websocket.close(code=1008)
        return

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        await websocket.close(code=1008)
        return

    host = authenticate_host(host_id, token)
    if host is None:
        # A disconnected host can learn that its saved credentials were revoked
        # when it reconnects after the account was logged out.
        await websocket.accept()
        await websocket.close(code=4001, reason="Account credentials revoked")
        return
    if host.password_hash != hashlib.sha256(password.encode()).hexdigest():
        await websocket.close(code=1008)
        return

    await websocket.accept()

    if host_id not in user_info:
        user_info[host_id] = AppUser(status=UserState.LOGGED_OUT, **host.model_dump())

    app_user = user_info[host_id]
    was_logged_out = app_user.status == UserState.LOGGED_OUT

    if app_user.client is None:
        string_session = decrypt_string(app_user.string_session, password)
        client = TelegramClient(StringSession(string_session), API_ID, API_HASH)
        try:
            await client.connect()
            cur_user = await client.get_me()
        except (OSError, TimeoutError):
            await client.disconnect()
            await websocket.close(code=1011, reason="Could not connect to Telegram")
            return
        if cur_user is None:
            await client.disconnect()
            await websocket.close(code=1011, reason="Telegram session is no longer authorized")
            return
        app_user.client = client
        user_name = get_user_name(cur_user)
        store_telegram_user(cur_user.id, user_name)

        user_preloading[app_user.user_id] = AppUserPreloading()
        await event_handlers.create_new_message_handler(app_user.user_id)
        await event_handlers.create_delete_message_handler(app_user.user_id)
        reset_idle_timer(app_user.user_id)
    if was_logged_out:
        app_user.status = UserState.AUTHENTICATED

    registered_host = Host(user=app_user, websocket=websocket, embedding_models=embedding_models)
    previous_host = available_hosts.get(host_id)
    available_hosts[host_id] = registered_host
    record_activity(host_id)

    groups = get_app_user_groups(host_id)
    for group in groups:
        hosts = available_group_hosts.get(group.group_id)
        if hosts is None:
            new_heap: list[Host] = list()
            heapify(new_heap)
            hosts = new_heap
            available_group_hosts[group.group_id] = new_heap
        heappush(hosts, registered_host)

    notify_disconnection = False
    try:
        if previous_host is not None:
            await disconnect_host(previous_host)
            await finish_requests(previous_host)
            await close_websocket(previous_host, notify=False)
        await websocket.send_json({"status": 200})
        try:
            await send_message(chat_id=host_id, text="Successfully connected.")
            await menu(user_id=host_id)
        except TelegramError:
            print(f"Could not notify host {host_id} that it connected.")
        while True:
            message = await websocket.receive_json()
            request_id = message.get("request_id")
            if request_id is None:
                continue
            request = active_requests.get(request_id)
            if not request or request.host is not registered_host:
                continue


            user_id = message.get("user_id")
            operation = message.get("operation")
            if message.get("status") == 102:
                completed, total = message.get("completed"), message.get("total")
                stage = message.get("stage")
                if (user_id == request.user_id and request.progress_queue is not None
                        and type(completed) is int and type(total) is int
                        and 0 <= completed <= total and total > 0
                        and stage in ("messages", "merging")):
                    # Keep only the newest update when the UI is slower than the host.
                    if request.progress_queue.full():
                        request.progress_queue.get_nowait()
                    request.progress_queue.put_nowait((completed, total, stage))
                continue
            result = message.get("result")
            if message.get("status") != 200:
                result = None
            elif user_id != request.user_id or not operation:
                continue
            active_requests.pop(request_id, None)
            if not request.future.done():
                request.future.set_result(result)
    except WebSocketDisconnect as error:
        print("host was disconnected")
        notify_disconnection = error.code != 1012
    finally:
        notify_disconnection = (notify_disconnection and not registered_host.disconnected
                                and available_hosts.get(host_id) is registered_host)
        await disconnect_host(registered_host)
        await finish_requests(registered_host)
        await close_websocket(registered_host, notify=False)
        if notify_disconnection:
            try:
                await menu(user_id=host_id, notice="Your host disconnected. Reconnect it to make your machine available again.")
            except TelegramError:
                print(f"Could not notify host {host_id} that it disconnected.")
