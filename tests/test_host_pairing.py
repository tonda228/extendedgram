import hashlib
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from starlette.websockets import WebSocket, WebSocketDisconnect
from telegram import Chat, Message

from utils.classes import AppUser, LoginStage, UninitializedUser, UserState


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_host_finishes_bot_signup_before_connecting(load_module, run_async, monkeypatch):
    auth = load_module("utils/auth.py")
    checks = load_module("utils/check_authentication.py")
    user = AppUser(user_id=123, status=UserState.LOGGED_OUT)
    user.client = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(id=123)))
    signup = UninitializedUser(status=LoginStage.AWAIT_BOT_PASSWORD_CONFIRMATION, is_host=True,
                               hashed_password=digest("password"))
    for module in (auth, checks):
        monkeypatch.setattr(module, "user_info", {123: user})
        monkeypatch.setattr(module, "uninitialized_users", {123: signup})
    monkeypatch.setattr(checks, "uninitialized_users", auth.uninitialized_users)

    run_async(auth.process_bot_password_confirmation(123, "password"))

    assert user.status == UserState.AUTHENTICATED
    assert not auth.uninitialized_users
    assert run_async(checks.check_authentication(123)) is True
    assert user.pairing_code_hash is not None
    auth.store_app_user.assert_called_once()


def test_restored_host_can_log_in_with_password_without_its_machine(load_module, run_async, monkeypatch):
    module = load_module("utils/auth.py")
    user = AppUser(user_id=123, is_host=True, status=UserState.LOGGED_OUT,
                   string_session="encrypted", password_hash=digest("password"))
    client = SimpleNamespace(connect=AsyncMock())
    monkeypatch.setattr(module, "user_info", {123: user})
    monkeypatch.setattr(module, "user_preloading", {})
    monkeypatch.setattr(module, "decrypt_string", lambda *args: "")
    monkeypatch.setattr(module, "TelegramClient", lambda *args: client)

    run_async(module.query_bot_password(user))
    assert "enter your bot password" in module.send_message.call_args.kwargs["text"]
    run_async(module.check_bot_password(123, "password"))

    assert user.status == UserState.AUTHENTICATED
    client.connect.assert_awaited_once()
    assert user.client is client


def test_password_login_then_host_connection_shows_menu_and_keeps_login_messages(load_module, run_async, monkeypatch):
    auth = load_module("utils/auth.py")
    server = load_module("server/server_runner.py")
    helpers = load_module("utils/helpers.py")
    menus = load_module("bot/commands/menu.py")
    user = AppUser(user_id=123, is_host=True, status=UserState.LOGGED_OUT,
                   string_session="encrypted", password_hash=digest("password"))
    for module in (auth, server, helpers, menus):
        monkeypatch.setattr(module, "user_info", {123: user})
    client = SimpleNamespace(connect=AsyncMock())
    monkeypatch.setattr(auth, "decrypt_string", lambda *args: "")
    monkeypatch.setattr(auth, "TelegramClient", lambda *args: client)
    monkeypatch.setattr(auth, "user_preloading", {})
    monkeypatch.setattr(sys.modules["bot.commands.menu"], "menu", menus.menu)
    for module in (auth, server, menus):
        module.send_message = helpers.send_message
    server.menu = menus.menu
    server.authenticate_host = Mock(return_value=SimpleNamespace(password_hash=digest("password")))
    sent = []

    async def send(chat_id, text, **kwargs):
        message = Message(message_id=len(sent) + 1, date=datetime.now(timezone.utc), chat=Chat(chat_id, "private"),
                          text=text, reply_markup=kwargs.get("reply_markup"))
        sent.append(message)
        return message

    helpers.application.bot.send_message.side_effect = send
    socket = WebSocket({"type": "websocket", "headers": [
        (b"authorization", b"Bearer token"), (b"password", b"password"),
        (b"embedding-model", b"test-embedding"),
    ]}, AsyncMock(), AsyncMock())
    socket.accept = AsyncMock()
    socket.send_json = AsyncMock()

    async def receive():
        # Stop after connecting without opening the separate disconnect menu.
        raise WebSocketDisconnect(code=1012)

    socket.receive_json = AsyncMock(side_effect=receive)

    async def flow():
        await auth.query_bot_password(user)
        await auth.check_bot_password(123, "password")
        await server.register_new_host(socket, 123)
        assert user.last_message is sent[-1]
        assert user.last_message.reply_markup is not None

    run_async(flow())

    assert [message.text for message in sent] == [
        "Please enter your bot password.", "Successfully logged in.",
        "Where do you wish to continue?", "Successfully connected.", "Where do you wish to continue?",
    ]
    helpers.application.bot.delete_message.assert_awaited_once_with(chat_id=123, message_id=3)
    assert user.temporary_message is None
    client.connect.assert_awaited_once()


def test_new_pairing_code_replaces_used_code_and_issues_token_without_websocket(load_module, run_async, monkeypatch):
    auth = load_module("utils/auth.py")
    server = load_module("server/server_runner.py")
    user = AppUser(user_id=123, is_host=True, status=UserState.AUTHENTICATED, password_hash=digest("password"))
    monkeypatch.setattr(auth, "user_info", {123: user})
    monkeypatch.setattr(auth, "uninitialized_users", {})
    monkeypatch.setattr(server, "user_info", auth.user_info)
    monkeypatch.setattr(auth.secrets, "token_urlsafe",
                        lambda size: next(codes if size == 8 else tokens))
    codes = iter(("first-code", "second-code"))
    tokens = iter(("first-token", "second-token"))
    server.cur.rowcount = 1

    def redeem(code):
        return run_async(server.check_pairing_code(server.PairingRequest(user_id=123, code=code, password="password")))

    run_async(auth.send_one_time_code(123))
    assert redeem("first-code") == {"token": "first-token"}
    with pytest.raises(HTTPException):
        redeem("first-code")
    run_async(auth.send_one_time_code(123))
    assert redeem("second-code") == {"token": "second-token"}
    assert user.token_hash == digest("second-token")
    assert user.status == UserState.AUTHENTICATED


@pytest.mark.parametrize("expired,password", [(True, "password"), (False, "wrong-password")])
def test_invalid_pairing_keeps_existing_token(load_module, run_async, monkeypatch, expired, password):
    module = load_module("server/server_runner.py")
    user = AppUser(user_id=123, is_host=True, status=UserState.AUTHENTICATED,
                   password_hash=digest("password"), token_hash=digest("existing-token"),
                   pairing_code_hash=digest("code"), pairing_code_expires_at=0 if expired else module.monotonic() + 600)
    monkeypatch.setattr(module, "user_info", {123: user})

    with pytest.raises(HTTPException):
        run_async(module.check_pairing_code(module.PairingRequest(user_id=123, code="code", password=password)))

    module.cur.execute.assert_not_called()
    assert user.token_hash == digest("existing-token")
