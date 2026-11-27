import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import httpx
import openai
from starlette.websockets import WebSocket

from utils.classes import AppUser, Host, Request, UserState


def make_host():
    socket = WebSocket({"type": "websocket"}, AsyncMock(), AsyncMock())
    return Host(user=AppUser(user_id=123, status=UserState.AUTHENTICATED), websocket=socket)


@pytest.mark.parametrize("for_summary", [False, True])
def test_1200_messages_use_one_model_request(load_module, run_async, for_summary):
    module = load_module("host/llm/completions.py")
    data = [{"type": "text", "text": f"Message {index}: " + "x" * 100} for index in range(1200)]
    topics = [{"start_message_id": 1, "end_message_id": 1200, "topic": "Plans", "summary": "A plan was agreed."}]
    create = module.llm_client.chat.completions.create
    create.return_value = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps(topics) if for_summary else "Answer"))])
    progress = AsyncMock()

    result = run_async(module.send_data(data, "Process messages", for_summary=for_summary, progress=progress))

    batches = [call.kwargs["messages"][1]["content"] for call in create.await_args_list]
    assert [len(batch) for batch in batches] == [1200]
    assert [item for batch in batches for item in batch] == data
    assert result == (topics if for_summary else ["Answer"])
    assert [call.args for call in progress.await_args_list] == [
        (0, 1200, "messages"), (1200, 1200, "messages")]


def test_removed_batch_settings_do_not_limit_large_requests(load_module, run_async, monkeypatch):
    monkeypatch.setenv("LLM_BATCH_MESSAGES", "1")
    monkeypatch.setenv("LLM_BATCH_CHARACTERS", "1000")
    module = load_module("host/llm/completions.py")
    data = [{"type": "text", "text": "я" * 30000} for _ in range(3)]
    create = module.llm_client.chat.completions.create
    create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Answer"))])

    assert run_async(module.send_data(data, "Search")) == ["Answer"]

    batches = [call.kwargs["messages"][1]["content"] for call in create.await_args_list]
    assert [len(batch) for batch in batches] == [3]
    assert [item for batch in batches for item in batch] == data


def test_large_request_is_split_only_after_actual_context_error(load_module, run_async):
    module = load_module("host/llm/completions.py")
    data = [{"type": "text", "text": str(index)} for index in range(1200)]
    create = module.llm_client.chat.completions.create
    processed = []

    async def complete(**kwargs):
        batch = kwargs["messages"][1]["content"]
        if len(batch) > 300:
            response = httpx.Response(400, request=httpx.Request("POST", "https://example.test"))
            raise openai.BadRequestError("Maximum context length exceeded", response=response, body=None)
        processed.extend(batch)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="A partial answer"))])

    create.side_effect = complete
    progress = AsyncMock()

    result = run_async(module.send_data(data, "Search", progress=progress))

    assert processed == data
    assert len(result) == 4
    assert create.await_count == 7
    progress.assert_awaited_with(1200, 1200, "messages")


def test_truncated_model_answer_is_split_without_losing_messages(load_module, run_async):
    module = load_module("host/llm/completions.py")
    data = [{"text": str(index)} for index in range(4)]
    answer = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Answer"))])
    create = module.llm_client.chat.completions.create
    create.side_effect = [SimpleNamespace(choices=[SimpleNamespace(finish_reason="length")]), answer, answer]

    assert run_async(module.send_data(data, "Search")) == ["Answer", "Answer"]
    assert [call.kwargs["messages"][1]["content"] for call in create.await_args_list] == [data, data[:2], data[2:]]


def test_invalid_model_configuration_does_not_retry_each_half(load_module, run_async):
    module = load_module("host/llm/completions.py")
    response = httpx.Response(400, request=httpx.Request("POST", "https://example.test"))
    module.llm_client.chat.completions.create.side_effect = openai.BadRequestError(
        "Invalid model configuration", response=response, body=None)

    with pytest.raises(openai.BadRequestError):
        run_async(module.send_data([{"text": "Hello"}] * 100, "Search"))

    assert module.llm_client.chat.completions.create.await_count == 1


def test_context_overflow_splits_batch_without_losing_messages(load_module, run_async):
    module = load_module("host/llm/completions.py")
    response = httpx.Response(400, request=httpx.Request("POST", "https://example.test"))
    answer = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Answer"))])
    module.llm_client.chat.completions.create.side_effect = [
        openai.BadRequestError("Maximum context length exceeded", response=response, body=None),
        answer, answer,
    ]
    data = [{"text": str(index)} for index in range(4)]

    assert run_async(module.send_data(data, "Search")) == ["Answer", "Answer"]
    batches = [call.kwargs["messages"][1]["content"]
               for call in module.llm_client.chat.completions.create.await_args_list]
    assert batches == [data, data[:2], data[2:]]


def test_search_keeps_partial_answers_when_merge_cannot_combine_them(load_module, run_async):
    module = load_module("host/host/search.py")
    module.send_data.return_value = ["First", "Second"]

    assert run_async(module.process_search_request([], "When?")) == "First\n\nSecond"

    assert module.send_data.await_count == 2


def test_progress_keeps_long_request_alive_without_resending(load_module, run_async, monkeypatch):
    module = load_module("server/requests.py")
    host = make_host()
    module.get_host.return_value = host
    monkeypatch.setattr(module, "REQUEST_TIMEOUT", 0.1)

    async def check():
        async def send(payload):
            request = module.active_requests[payload["request_id"]]

            async def process():
                for completed in (0, 100, 200, 300):
                    await asyncio.sleep(0.04)
                    request.progress_queue.put_nowait((completed, 300, "messages"))
                request.future.set_result("Done")

            worker = asyncio.create_task(process())
            workers.append(worker)

        workers = []
        host.websocket.send_json = AsyncMock(side_effect=send)
        assert await module.send_request(123, [], "search", "When?") == "Done"
        await asyncio.gather(*workers)
        host.websocket.send_json.assert_awaited_once()
        assert host.requests_count == 0
        assert not module.active_requests
        module.disconnect_host.assert_not_awaited()
        module.resend_processing_status.assert_awaited()
        module.send_message.return_value.delete.assert_awaited_once()

    run_async(check())


def test_model_error_does_not_disconnect_host_or_repeat_request(load_module, run_async):
    module = load_module("server/requests.py")
    host = make_host()
    module.get_host.return_value = host

    async def send(payload):
        module.active_requests[payload["request_id"]].future.set_result(None)

    host.websocket.send_json = AsyncMock(side_effect=send)
    assert run_async(module.send_request(123, [], "summarize")) is None
    host.websocket.send_json.assert_awaited_once()
    module.disconnect_host.assert_not_awaited()
    assert host.requests_count == 0


def test_cancelled_preload_drains_embedding_without_disconnecting_host(load_module, run_async):
    module = load_module("server/requests.py")
    host = make_host()
    module.get_host.return_value = host

    async def check():
        sent = asyncio.Event()
        requests = []

        async def send(payload):
            requests.append(module.active_requests[payload["request_id"]])
            if len(requests) == 1:
                sent.set()
            else:
                requests[-1].future.set_result([0.2])

        host.websocket.send_json = AsyncMock(side_effect=send)
        preload = asyncio.create_task(module.send_request(123, "Preload", "embedding"))
        await sent.wait()
        preload.cancel()
        search = asyncio.create_task(module.send_request(123, "Search", "embedding"))
        await asyncio.sleep(0)
        assert len(requests) == 1
        requests[0].future.set_result([0.1])
        with pytest.raises(asyncio.CancelledError):
            await preload
        assert await search == [0.2]
        assert host.requests_count == 0
        assert not module.active_requests
        module.disconnect_host.assert_not_awaited()

    run_async(check())


def test_timeout_sends_model_request_only_once(load_module, run_async, monkeypatch):
    module = load_module("server/requests.py")
    host = make_host()
    module.get_host.return_value = host
    host.websocket.send_json = AsyncMock()
    monkeypatch.setattr(module, "REQUEST_TIMEOUT", 0.01)

    assert run_async(module.send_request(123, [], "summarize")) is None
    host.websocket.send_json.assert_awaited_once()
    module.disconnect_host.assert_awaited_once_with(host)
    assert host.requests_count == 0
    assert not module.active_requests


def test_waiting_for_busy_host_does_not_start_request_timeout(load_module, run_async, monkeypatch):
    module = load_module("server/requests.py")
    host = make_host()
    module.get_host.return_value = host
    monkeypatch.setattr(module, "REQUEST_TIMEOUT", 0.01)

    async def check():
        async def reply(payload):
            module.active_requests[payload["request_id"]].future.set_result("Done")

        host.websocket.send_json = AsyncMock(side_effect=reply)
        await host.request_lock.acquire()
        task = asyncio.create_task(module.send_request(123, [], "summarize"))
        await asyncio.sleep(0.04)
        host.websocket.send_json.assert_not_awaited()
        assert not task.done()
        host.request_lock.release()
        assert await task == "Done"

    run_async(check())


def test_embedding_batch_uses_large_request_timeout(load_module, run_async, monkeypatch):
    module = load_module("server/requests.py")
    host = make_host()
    module.get_host.return_value = host
    monkeypatch.setattr(module, "REQUEST_TIMEOUT", 0.01)
    monkeypatch.setattr(module, "MODEL_REQUEST_TIMEOUT", 1)

    async def flow():
        workers = []

        async def reply(payload):
            async def finish():
                await asyncio.sleep(0.03)
                module.active_requests[payload["request_id"]].future.set_result([[0.1], [0.2]])
            workers.append(asyncio.create_task(finish()))

        host.websocket.send_json = AsyncMock(side_effect=reply)
        assert await module.send_request(123, ["First", "Second"], "embedding") == [[0.1], [0.2]]
        await asyncio.gather(*workers)
        module.disconnect_host.assert_not_awaited()

    run_async(flow())


def test_cancelled_model_request_cleans_up_connection(load_module, run_async):
    module = load_module("server/requests.py")
    host = make_host()
    module.get_host.return_value = host

    async def check():
        sent = asyncio.Event()

        async def send(payload):
            sent.set()

        host.websocket.send_json = AsyncMock(side_effect=send)
        task = asyncio.create_task(module.send_request(123, [], "summarize"))
        await sent.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert host.requests_count == 0
        assert not host.request_lock.locked()
        assert not module.active_requests
        module.close_websocket.assert_awaited_once_with(host, notify=False)

    run_async(check())


def test_cancellation_does_not_broadcast_planned_shutdown(load_module, run_async):
    module = load_module("utils/infinite_task.py")
    module.send_shutdown_notification = AsyncMock()

    async def check():
        task = asyncio.create_task(module.get_infinite_task())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run_async(check())
    module.send_shutdown_notification.assert_not_awaited()


def test_host_sends_progress_before_final_response(load_module, run_async):
    module = load_module("host/main.py")
    request = {"user_id": 123, "request_id": 42, "operation": "summarize", "data": [{"text": "Hello"}]}
    socket = SimpleNamespace(
        recv=AsyncMock(side_effect=[json.dumps({"status": 200}), json.dumps(request), EOFError()]),
        send=AsyncMock())

    async def summarize(data, progress):
        await progress(1, 1, "messages")
        return [{"topic": "Hello"}]

    module.process_summarize_request = AsyncMock(side_effect=summarize)

    async def check():
        with pytest.raises(EOFError):
            await module.keep_host_running(socket)
        # The per-request heartbeat must not outlive the completed request.
        assert len(asyncio.all_tasks()) == 1

    run_async(check())
    sent = [json.loads(call.args[0]) for call in socket.send.await_args_list]
    assert [message["status"] for message in sent] == [102, 102, 200]
    assert sent[1]["completed"] == 1
    assert all(message["request_id"] == 42 for message in sent)


def test_progress_has_absolute_deadline_even_with_active_host(load_module, run_async, monkeypatch):
    module = load_module("server/requests.py")
    monkeypatch.setattr(module, "MODEL_REQUEST_TIMEOUT", 0.03)

    async def check():
        request = Request(user_id=123, host=make_host(),
                          future=asyncio.get_running_loop().create_future(),
                          progress_queue=asyncio.Queue())

        async def heartbeat():
            while True:
                await request.progress_queue.put((0, 100, "messages"))
                await asyncio.sleep(0.005)

        task = asyncio.create_task(heartbeat())
        try:
            with pytest.raises(TimeoutError):
                await module.wait_for_model(request, None)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            request.future.cancel()

    run_async(check())
