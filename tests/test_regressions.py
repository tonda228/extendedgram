import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import openai
import pytest
from starlette.websockets import WebSocket, WebSocketDisconnect
from telethon import TelegramClient
from telethon.sessions import StringSession

from utils.classes import AppUser, Host, Request, UninitializedUser, UserState


def test_completed_cooldown_restores_attempts(run_async, monkeypatch):
    async def check():
        user = UninitializedUser()
        timer = asyncio.create_task(asyncio.sleep(0))
        await timer
        user._init_info.tries_left = 0
        user._init_info.wait_task = timer

        assert user.tries_left == 5
        assert user.wait is False
        assert user._init_info.wait_task is None

    run_async(check())


def test_first_login_initializes_preloading(load_module, run_async, monkeypatch):
    module = load_module("utils/auth.py")
    user = AppUser(user_id=123, status=UserState.LOGGED_OUT)
    monkeypatch.setattr(module, "user_info", {123: user})
    monkeypatch.setattr(module, "user_preloading", {})
    module.query_new_bot_password = AsyncMock()

    run_async(module.successful_login(123))

    assert 123 in module.user_preloading
    module.event_handlers.create_new_message_handler.assert_awaited_once_with(123)
    module.event_handlers.create_delete_message_handler.assert_awaited_once_with(123)


def test_two_factor_login_uses_the_same_initialization(load_module, run_async, monkeypatch):
    module = load_module("utils/auth.py")
    client = SimpleNamespace(sign_in=AsyncMock())
    monkeypatch.setattr(module, "user_info", {123: SimpleNamespace(client=client)})
    module.successful_login = AsyncMock()

    run_async(module.process_password(123, "password"))

    client.sign_in.assert_awaited_once_with(password="password")
    module.successful_login.assert_awaited_once_with(123)


def test_invalid_json_uses_default_retry_limit(load_module, run_async):
    module = load_module("host/llm/completions.py")
    create = module.llm_client.chat.completions.create
    create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="invalid JSON"))])

    with pytest.raises(ValueError):
        run_async(module.send_data([], "Summarize", for_summary=True))

    assert create.await_count == 5


def test_single_message_bad_request_does_not_recurse(load_module, run_async):
    module = load_module("host/llm/completions.py")
    response = httpx.Response(400, request=httpx.Request("POST", "https://example.test"))
    error = openai.BadRequestError("Too large", response=response, body=None)
    module.llm_client.chat.completions.create.side_effect = error

    with pytest.raises(openai.BadRequestError):
        run_async(module.send_data([{"type": "text", "text": "Large message"}], "Search"))

    assert module.llm_client.chat.completions.create.await_count == 1


def test_config_loads_from_another_directory(load_module, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    module = load_module("utils/config.py")

    assert module.config_file["page_size"] > 0


def test_private_message_insert_includes_topic_id(load_module, run_async):
    module = load_module("database/messages.py")
    message = SimpleNamespace(id=42, action=None, text="Hello", date=None)
    dialog = (SimpleNamespace(id=123), None)
    module.utils = SimpleNamespace(get_peer_id=lambda peer: peer.id)

    run_async(module.store_private_message(message, dialog, 456, None, None, None))

    query, values = module.cur.execute.call_args.args
    assert "topic_id" in query
    assert values[:4] == (42, 123, 456, 0)
    assert query.count("%s") == len(values)


def test_request_timeout_cleans_up_pending_requests(load_module, run_async, monkeypatch):
    helpers = load_module("server/__init__.py")
    module = load_module("server/requests.py")
    helpers.available_hosts = {123: None}
    helpers.active_requests = module.active_requests
    monkeypatch.setattr(module, "disconnect_host", helpers.disconnect_host)
    monkeypatch.setattr(module, "finish_requests", helpers.finish_requests)
    websocket = WebSocket({"type": "websocket"}, AsyncMock(), AsyncMock())
    user = AppUser(user_id=123, status=UserState.AUTHENTICATED)
    host = Host(user=user, websocket=websocket)
    module.get_host.side_effect = [host, None]
    helpers.available_hosts[123] = host
    monkeypatch.setattr(module, "REQUEST_TIMEOUT", 0.001)
    monkeypatch.setattr(websocket, "send_json", AsyncMock())
    monkeypatch.setattr(websocket, "close", AsyncMock())

    assert run_async(module.send_request(123, "Hello", "search")) is None
    assert module.active_requests == {}
    assert helpers.available_hosts == {}


@pytest.mark.parametrize("reply_with_error", [True, False, "progress"])
def test_group_host_receives_requests_and_cleans_up_on_disconnect(load_module, run_async, monkeypatch, reply_with_error):
    helpers = load_module("server/__init__.py")
    module = load_module("server/server_runner.py")
    helpers.available_hosts = module.available_hosts
    helpers.available_group_hosts = module.available_group_hosts
    helpers.active_requests = module.active_requests
    monkeypatch.setattr(module, "disconnect_host", helpers.disconnect_host)
    monkeypatch.setattr(module, "finish_requests", helpers.finish_requests)
    monkeypatch.setattr(module, "close_websocket", AsyncMock())
    module.get_app_user_groups.return_value = [SimpleNamespace(group_id=1)]
    user = AppUser(user_id=123, status=UserState.WAIT_FOR_GROUP_CHOICE,
                   client=TelegramClient(StringSession(), 1, "test-hash"))
    monkeypatch.setattr(module, "user_info", {123: user})
    module.authenticate_host = Mock(return_value=SimpleNamespace(password_hash=hashlib.sha256(b"password").hexdigest()))
    websocket = WebSocket({"type": "websocket", "headers": [
        (b"authorization", b"Bearer token"), (b"password", b"password"),
        (b"embedding-models", b'["another-embedding", "second-embedding"]'),
    ]}, AsyncMock(), AsyncMock())
    monkeypatch.setattr(websocket, "accept", AsyncMock())
    futures = []

    async def register_request(payload):
        assert module.available_hosts[123].embedding_models == ("another-embedding", "second-embedding")
        assert user.status == UserState.WAIT_FOR_GROUP_CHOICE
        module.menu.assert_not_awaited()
        future = asyncio.get_running_loop().create_future()
        futures.append(future)
        module.active_requests[42] = Request(user_id=456, host=module.available_hosts[123], future=future,
                                             progress_queue=asyncio.Queue(maxsize=1))

    monkeypatch.setattr(websocket, "send_json", AsyncMock(side_effect=register_request))
    if reply_with_error == "progress":
        responses = [
            {"request_id": 42, "status": 102, "user_id": 456, "operation": "summarize",
             "completed": 100, "total": 1200, "stage": "messages"},
            {"request_id": 42, "status": 200, "user_id": 456, "operation": "summarize", "result": "Done"},
        ]
    else:
        responses = [{"request_id": 42, "status": 404}] if reply_with_error else []
    messages = iter(responses + [WebSocketDisconnect()])

    async def receive():
        message = next(messages)
        if reply_with_error == "progress" and isinstance(message, dict) and message.get("status") == 200:
            # An intermediate update must leave the result future pending.
            pending = module.active_requests[42]
            assert not pending.future.done()
            assert pending.progress_queue.get_nowait() == (100, 1200, "messages")
        if isinstance(message, Exception):
            raise message
        return message

    receiver = AsyncMock(side_effect=receive)
    monkeypatch.setattr(websocket, "receive_json", receiver)

    run_async(module.register_new_host(websocket, 123))

    assert module.menu.await_count == 2
    module.menu.assert_any_await(user_id=123)
    module.menu.assert_any_await(
        user_id=123, notice="Your host disconnected. Reconnect it to make your machine available again.")
    assert receiver.await_count == len(responses) + 1
    assert futures[0].done()
    assert futures[0].result() == ("Done" if reply_with_error == "progress" else None)
    assert module.active_requests == {}
    assert module.available_hosts == {}
    assert module.available_group_hosts[1] == []
