import hashlib
import os
import secrets

from telethon import TelegramClient
from telethon.sessions import StringSession

from bot.commands.menu import menu
from database import cur, connection
from fastapi import WebSocket, WebSocketDisconnect, HTTPException
from fastapi.applications import AppType
from fastapi.security import HTTPBearer

from database.users import store_telegram_user
from server import available_hosts, active_requests
from utils.check_authentication import offer_group_options
from utils.classes import Host, UserState, AppUser
from utils.helpers import get_user_name, send_message
from utils.security import decrypt_string
from utils.state import user_info, uninitialized_users
from pydantic import BaseModel

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]

bearer = HTTPBearer()

def create_api_handlers(app: AppType):
    app.post("/pair")(check_pairing_code)
    app.websocket("/ws/{host_id}")(register_new_host)

def authenticate_host(host_id, token: str):
    cur.execute("""
    SELECT *
    FROM app_user
    WHERE user_id = %s AND is_host = TRUE
    """, (host_id,))
    host = cur.fetchone()
    if host is None:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    if host.token_hash != token_hash:
        return None
    return host

class PairingRequest(BaseModel):
    user_id: int
    code: str


async def check_pairing_code(info: PairingRequest):
    user_id = info.user_id
    pairing_code = info.code

    if user_id not in user_info or not user_info[user_id].is_host:
        raise HTTPException(status_code=404, detail="Host not found")
    app_user = user_info[user_id]
    hashed = app_user.pairing_code_hash
    if hashed != hashlib.sha256(pairing_code.encode()).hexdigest():
        raise HTTPException(status_code=404, detail="Hashes are different.")

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    cur.execute("""
    UPDATE app_user
    SET token_hash = %s
    WHERE user_id = %s
    """, (token_hash, user_id))
    connection.commit()

    password = uninitialized_users[user_id].bot_password
    app_user.token_hash = token_hash

    await offer_group_options(user_id)
    return {"token": token, "password": password}


async def register_new_host(websocket: WebSocket, host_id: int):
    authorization = websocket.headers.get("authorization")
    password = websocket.headers.get("password")
    if authorization is None or password is None:
        await websocket.close(code=1008)
        return

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        await websocket.close(code=1008)
        return

    host = authenticate_host(host_id, token)
    if host is None or host.password_hash != hashlib.sha256(password.encode()).hexdigest():
        await websocket.close(code=1008)
        return

    await websocket.accept()

    if host_id not in user_info:
        user_info[host_id] = AppUser(host_id, UserState.AUTHENTICATED, app_user=host)

    app_user = user_info[host_id]

    if app_user.client is None:
        string_session = decrypt_string(app_user.string_session, password)
        user_info[host_id].client = TelegramClient(StringSession(string_session), API_ID, API_HASH)
        await user_info[host_id].client.connect()
        user_info[host_id].status = UserState.AUTHENTICATED

        cur_user = await app_user.client.get_me()
        user_name = get_user_name(cur_user)
        store_telegram_user(cur_user.id, user_name)

    await send_message(chat_id=host_id, text="Successfully connected.")

    uninitialized_user = uninitialized_users.get(host_id)
    if uninitialized_user:
        if uninitialized_user.in_group:
            await offer_group_options(host_id)
            return
        else:
            del uninitialized_users[host_id]

    await menu(user_id=host_id)
    available_hosts[host_id] = Host(host, websocket)

    await websocket.send_json({"status": 200})

    try:
        while True:
            message = await websocket.receive_json()
            request_id = message.get("request_id")
            if request_id is None:
                return
            request = active_requests[request_id]
            if not request_id or host_id != request.host.user.user_id:
                continue


            user_id = message.get("user_id")
            operation = message.get("operation")
            result = message.get("result")
            if message.get("status") != 200 or not operation or not result:
                # handle this properly
                if not user_id:
                    return
                await send_message(chat_id=message["user_id"], text="Error on occurred on host side. Please try again later.")

            del active_requests[request_id]
            request.future.set_result(result)
    except WebSocketDisconnect:
        print("host was disconnected")
        if host_id in available_hosts:
            del available_hosts[host_id]