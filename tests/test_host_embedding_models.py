import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def test_host_advertises_all_configured_models(load_module, run_async, monkeypatch):
    module = load_module("host/main.py")
    monkeypatch.setenv("EMBEDDING_MODELS", "model-a, model-b, model-a")
    socket = SimpleNamespace(wait_closed=AsyncMock(), close=AsyncMock(), close_code=1000)
    module.keep_host_running = AsyncMock()
    connect = AsyncMock(return_value=socket)
    monkeypatch.setattr(module.websockets, "connect", connect)

    run_async(module.open_connection({"user_id": 123, "token": "fake", "password": "fake"}))

    headers = connect.call_args.kwargs["additional_headers"]
    assert json.loads(headers["Embedding-Models"]) == ["model-a", "model-b"]
    assert headers["Embedding-Model"] == "model-a"


def test_host_uses_requested_model_and_rejects_unsupported_model(load_module, run_async):
    module = load_module("host/main.py")
    request = {"operation": "embedding", "user_id": 123, "data": "Hello"}
    incoming = [
        {"status": 200},
        dict(request, request_id=1, embedding_model="model-b"),
        dict(request, request_id=2, embedding_model="unsupported"),
        dict(request, request_id=3),
    ]
    socket = SimpleNamespace(recv=AsyncMock(side_effect=[*map(json.dumps, incoming), EOFError()]), send=AsyncMock())
    module.create_embedding.return_value = [0.1]

    with pytest.raises(EOFError):
        run_async(module.keep_host_running(socket, ("model-a", "model-b")))

    assert [call.args for call in module.create_embedding.await_args_list] == [("Hello", "model-b"), ("Hello", "model-a")]
    responses = [json.loads(call.args[0]) for call in socket.send.await_args_list]
    assert [response["status"] for response in responses] == [200, 404, 200]


def test_coordinator_sends_selected_model_to_host(load_module, run_async):
    from starlette.websockets import WebSocket
    from utils.classes import AppUser, Host, UserState

    module = load_module("server/requests.py")
    socket = WebSocket({"type": "websocket"}, AsyncMock(), AsyncMock())
    host = Host(user=AppUser(user_id=123, status=UserState.AUTHENTICATED), websocket=socket,
                embedding_models=("model-a", "model-b"))
    module.get_host.return_value = host

    async def reply(payload):
        module.active_requests[payload["request_id"]].future.set_result([0.1] * 1024)

    socket.send_json = AsyncMock(side_effect=reply)

    vector = run_async(module.request_embedding(456, "Hello", "model-b"))

    assert vector.dimensions() == 1024
    module.get_host.assert_awaited_once_with(456, embedding_model="model-b")
    assert socket.send_json.call_args.args[0]["embedding_model"] == "model-b"
