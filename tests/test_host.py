import asyncio
import json
import runpy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest


def test_search_returns_host_answer(load_module, run_async):
    module = load_module("host/host/search.py")
    module.send_data.return_value = ["Friday"]
    data = [{"type": "text", "text": "The deadline is Friday."}]

    result = run_async(module.process_search_request(data, "When is the deadline?"))

    assert result == "Friday"
    assert module.send_data.call_args.args[0] == data
    assert "When is the deadline?" in module.send_data.call_args.args[1]
    assert "<b>, <i>, and <blockquote>" in module.send_data.call_args.args[1]
    assert "Do not use Markdown" in module.send_data.call_args.args[1]


def test_search_combines_partial_answers(load_module, run_async):
    module = load_module("host/host/search.py")
    module.send_data.side_effect = [["Friday", "At noon"], ["Friday at noon"]]

    result = run_async(module.process_search_request([], "When?"))

    assert result == "Friday at noon"
    assert module.send_data.await_count == 2
    assert module.send_data.call_args.args[0] == [
        {"type": "text", "text": "Friday"},
        {"type": "text", "text": "At noon"},
    ]


def test_summary_returns_topics(load_module, run_async):
    module = load_module("host/host/summarize.py")
    topics = [{"start_message_id": 1, "end_message_id": 2, "topic": "Deadline", "summary": "Submit on Friday."}]
    module.send_data.return_value = topics

    assert run_async(module.process_summarize_request([])) == topics
    assert module.send_data.await_count == 1


def test_summary_combines_more_than_ten_topics(load_module, run_async):
    module = load_module("host/host/summarize.py")
    topics = [{"start_message_id": 1, "end_message_id": 2, "topic": str(index), "summary": "A decision"} for index in range(11)]
    combined = [{"start_message_id": 1, "end_message_id": 2, "topic": "Decisions", "summary": "Combined summary"}]
    module.send_data.side_effect = [topics, combined]

    assert run_async(module.process_summarize_request([])) == combined
    assert module.send_data.await_count == 2
    sent_data = module.send_data.call_args.args[0]
    assert len(sent_data) == 11
    assert json.loads(sent_data[0]["text"]) == topics[0]


def test_summary_retries_once_when_merge_does_not_reduce_topics(load_module, run_async):
    module = load_module("host/host/summarize.py")
    topics = [{"start_message_id": i, "end_message_id": i, "topic": str(i), "summary": "A decision"}
              for i in range(1, 21)]
    module.send_data.side_effect = [topics, topics, topics[:5]]

    assert run_async(module.process_summarize_request([])) == topics[:5]
    assert module.send_data.await_count == 3
    assert "no more than 5" in module.send_data.call_args.args[1]


@pytest.mark.parametrize("failed_merge", ["unchanged", "empty", "invalid"])
def test_summary_keeps_valid_topics_when_merge_fails(load_module, run_async, failed_merge):
    module = load_module("host/host/summarize.py")
    topics = [{"start_message_id": i, "end_message_id": i, "topic": str(i), "summary": "A decision"}
              for i in range(1, 21)]
    failure = {"unchanged": topics, "empty": [], "invalid": [{"topic": "Missing IDs"}]}[failed_merge]
    module.send_data.side_effect = [topics, failure, failure]

    result = run_async(module.process_summarize_request([]))

    assert len(result) == 10
    assert all(topic in topics for topic in result)
    assert result[0] == topics[0] and result[-1] == topics[-1]


def test_pairing_and_websocket_use_configured_coordinator(load_module, run_async, monkeypatch):
    monkeypatch.setenv("COORDINATOR_URL", "https://coordinator.example.test/")
    module = load_module("host/main.py")
    monkeypatch.setattr("builtins.input", Mock(side_effect=["123", "fake-code", "n"]))
    monkeypatch.setattr(module, "getpass", Mock(return_value="fake-password"))
    response = Mock()
    response.json.return_value = {"token": "fake-token"}
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.return_value = response
    monkeypatch.setattr(module.httpx, "AsyncClient", Mock(return_value=client))
    socket = SimpleNamespace(wait_closed=AsyncMock(), close_code=1000, close=AsyncMock())
    connect = AsyncMock(return_value=socket)
    monkeypatch.setattr(module.websockets, "connect", connect)
    module.keep_host_running = AsyncMock()

    user_id, token, password = run_async(module.get_token())[0]
    run_async(module.open_connection({"user_id": user_id, "token": token, "password": password}))

    client.post.assert_awaited_once_with("https://coordinator.example.test/pair",
                                         json={"user_id": 123, "code": "fake-code", "password": "fake-password"})
    assert connect.call_args.args[0] == "wss://coordinator.example.test/ws/123"


def test_unreachable_coordinator_shows_pairing_error(load_module, run_async, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AUTH_FILE", str(tmp_path / "auth.json"))
    module = load_module("host/main.py")
    module.get_token = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

    run_async(module.open_connections())

    assert "Could not reach the coordinator" in capsys.readouterr().out
    assert not module.AUTH_FILE.exists()


def test_missing_server_address_exits_before_prompting_for_credentials(load_module, run_async, monkeypatch, capsys):
    monkeypatch.delenv("COORDINATOR_URL", raising=False)
    module = load_module("host/main.py")
    module.get_token = AsyncMock()
    module.open_connection = AsyncMock()
    module.offer_another_user = Mock(side_effect=AssertionError("No account prompt without a server URL"))

    run_async(module.open_connections())

    assert "Set COORDINATOR_URL in host/.env" in capsys.readouterr().out
    module.get_token.assert_not_awaited()
    module.open_connection.assert_not_awaited()
    module.offer_another_user.assert_not_called()


@pytest.mark.parametrize("error,expected", [
    (KeyboardInterrupt(), "Host stopped."),
    (EOFError(), "Terminal input was closed."),
])
def test_host_entrypoint_handles_terminal_stop_without_traceback(load_module, monkeypatch, capsys, error, expected):
    module = load_module("host/main.py")

    def interrupt(coroutine):
        coroutine.close()
        raise error

    monkeypatch.setattr(module.asyncio, "run", interrupt)

    runpy.run_path(module.__file__, run_name="__main__")

    output = capsys.readouterr()
    assert expected in output.out
    assert "Traceback" not in output.err


def test_host_handles_network_error_during_processing_and_closes_socket(load_module, run_async, monkeypatch, capsys):
    module = load_module("host/main.py")

    async def flow():
        socket = SimpleNamespace(wait_closed=AsyncMock(side_effect=asyncio.Event().wait),
                                 close_code=1000, close=AsyncMock(side_effect=OSError("Connection closed")))
        module.keep_host_running = AsyncMock(side_effect=OSError("Connection reset"))
        monkeypatch.setattr(module.websockets, "connect", AsyncMock(return_value=socket))

        await module.open_connection({"user_id": 123, "token": "token", "password": "password"})

        socket.close.assert_awaited_once()
        assert len(asyncio.all_tasks()) == 1

    run_async(flow())
    assert "Disconnected: Connection reset" in capsys.readouterr().out
