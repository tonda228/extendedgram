import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock
from starlette.websockets import WebSocket

from utils.classes import AppUser, Host, UserState


def test_request_returns_none_when_no_host_is_available(load_module, run_async):
    module = load_module("server/requests.py")

    assert run_async(module.send_request(123, "Hello", "search")) is None
    module.get_host.assert_awaited_once_with(123)
    assert module.active_requests == {}


def test_request_registers_pending_response_and_returns_result(load_module, run_async, monkeypatch):
    module = load_module("server/requests.py")
    websocket = WebSocket({"type": "websocket"}, AsyncMock(), AsyncMock())
    user = AppUser(user_id=123, status=UserState.AUTHENTICATED)
    host = Host(user=user, websocket=websocket)
    module.get_host.return_value = host

    async def reply(payload):
        request = module.active_requests.pop(payload["request_id"])
        assert request.user_id == 123
        assert request.host is host
        request.future.set_result("Friday")

    sender = AsyncMock(side_effect=reply)
    monkeypatch.setattr(websocket, "send_json", sender)

    assert run_async(module.send_request(123, "Hello", "search", "When?")) == "Friday"
    assert sender.call_args.args[0]["data"] == "Hello"
    sender.assert_awaited_once()
    assert host.requests_count == 0
    assert module.active_requests == {}


def test_image_data_url_contains_file_contents(load_module, tmp_path):
    module = load_module("server/requests.py")
    image = tmp_path / "photo.png"
    image.write_bytes(b"image-contents")

    result = module.image_to_data_url(image)

    prefix, encoded = result.split(",", 1)
    assert prefix == "data:image/png;base64"
    assert base64.b64decode(encoded) == b"image-contents"


def test_embedding_request_includes_text_and_media(load_module, run_async):
    module = load_module("server/requests.py")
    module.send_request = AsyncMock(return_value=[0.1] * 1024)
    message = SimpleNamespace(text="Hello")

    result = run_async(module.request_message_embedding((None, None), message, 123, "A cat"))

    module.send_request.assert_awaited_once_with(123, "Text: Hello\nMedia: A cat", "embedding")
    assert len(result.to_list()) == 1024
    module.cur.execute.assert_not_called()


def test_query_embedding_uses_host_and_returns_vector(load_module, run_async):
    module = load_module("server/requests.py")
    module.send_request = AsyncMock(return_value=[0.1] * 1024)

    result = run_async(module.request_embedding(123, "When is the meeting?"))

    module.send_request.assert_awaited_once_with(123, "When is the meeting?", "embedding")
    assert len(result.to_list()) == 1024


def test_query_embedding_returns_none_without_a_result(load_module, run_async):
    module = load_module("server/requests.py")
    module.send_request = AsyncMock(return_value=None)

    assert run_async(module.request_embedding(123, "Question")) is None
