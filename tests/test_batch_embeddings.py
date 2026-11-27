from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from pgvector import Vector
import httpx
import openai
import pytest


def test_1200_new_messages_use_one_embedding_request(load_module, run_async, monkeypatch):
    module = load_module("database/messages.py")
    monkeypatch.setitem(module.user_info, 123, SimpleNamespace(history_size=100, process_images=False))
    module.get_saved_message_metadata = Mock(return_value={})
    module.store_prepared_message = AsyncMock()
    produced = []

    async def embed(user_id, texts, model):
        vectors = [object() for text in texts]
        produced.extend(vectors)
        return vectors

    module.request_embeddings.side_effect = embed

    class FakeClient:
        async def iter_messages(self, *args, **kwargs):
            for index in range(1200):
                yield SimpleNamespace(id=index, text=str(index), photo=None, date=datetime.now(timezone.utc))

    dialog = (SimpleNamespace(id=10, entity=None), None)
    assert run_async(module.store_unsaved_messages(123, dialog, FakeClient(), True, show_download_status=False))

    batches = [call.args[1] for call in module.request_embeddings.await_args_list]
    assert [len(batch) for batch in batches] == [1200]
    assert [text for batch in batches for text in batch] == [f"Text: {index}\nMedia: None" for index in range(1200)]
    stored = module.store_prepared_message.await_args_list
    assert [call.args[0].id for call in stored] == list(range(1200))
    assert [call.args[4] for call in stored] == produced
    module.request_message_embedding.assert_not_awaited()


@pytest.mark.parametrize("model", ["test-embedding", "group-model"])
@pytest.mark.parametrize("public", [False, True])
def test_cached_vectors_keep_their_message_scope_and_model(load_module, run_async, monkeypatch, model, public):
    module = load_module("database/messages.py")
    monkeypatch.setitem(module.user_info, 123, SimpleNamespace(history_size=100, process_images=False))
    module.get_saved_message_metadata = Mock(return_value={1: (None, False), 2: (None, True)})
    module.get_model_embedding_ids = Mock(return_value={2})
    module.store_model_embedding = Mock()
    module.store_prepared_message = AsyncMock()
    vector = Vector([0.1] * 1024)
    module.request_embeddings.return_value = [vector]

    class FakeChannel:
        id = 20

    monkeypatch.setattr(module, "Channel", FakeChannel)

    class FakeClient:
        async def iter_messages(self, *args, **kwargs):
            for index in (2, 1):
                yield SimpleNamespace(id=index, text=str(index), photo=None, date=datetime.now(timezone.utc))

    dialog = (SimpleNamespace(id=10, entity=FakeChannel() if public else None), SimpleNamespace(id=30))
    assert run_async(module.store_unsaved_messages(123, dialog, FakeClient(), True,
                                                  embedding_model=model, show_download_status=False))

    module.request_embeddings.assert_awaited_once_with(123, ["Text: 1\nMedia: None"], model)
    module.store_prepared_message.assert_not_awaited()
    if model == "group-model":
        module.store_model_embedding.assert_called_once_with(dialog, 123, 1, model, vector)
        module.cur.executemany.assert_not_called()
    else:
        query, rows = module.cur.executemany.call_args.args
        assert ("UPDATE public_message" if public else "UPDATE private_message") in query
        assert rows == [(vector, 1, 20, 30) if public else (vector, 1, 10, 123, 30)]
    module.connection.commit.assert_called_once()


@pytest.mark.parametrize("result", [None, []])
def test_failed_embedding_batch_is_not_saved(load_module, run_async, monkeypatch, result):
    module = load_module("database/messages.py")
    monkeypatch.setitem(module.user_info, 123, SimpleNamespace(history_size=100, process_images=False))
    module.get_saved_message_metadata = Mock(return_value={})
    module.store_prepared_message = AsyncMock()
    module.request_embeddings.return_value = result

    class FakeClient:
        async def iter_messages(self, *args, **kwargs):
            yield SimpleNamespace(id=1, text="Hello", photo=None, date=datetime.now(timezone.utc))

    assert not run_async(module.store_unsaved_messages(123, (SimpleNamespace(entity=None), None), FakeClient(), True,
                                                      show_download_status=False))
    module.store_prepared_message.assert_not_awaited()
    module.cur.executemany.assert_not_called()
    module.connection.commit.assert_not_called()


@pytest.mark.parametrize("enabled", [False, True])
def test_new_photos_are_described_once_and_included_in_embedding_batch(load_module, run_async, monkeypatch, enabled, tmp_path):
    monkeypatch.chdir(tmp_path)
    module = load_module("database/messages.py")
    monkeypatch.setitem(module.user_info, 123, SimpleNamespace(history_size=100, process_images=enabled))
    module.get_saved_message_metadata = Mock(return_value={})
    module.store_prepared_message = AsyncMock()
    module.request_embeddings.return_value = [object()]
    module.request_media_description.return_value = "A cat"

    class FakeClient:
        async def iter_messages(self, *args, **kwargs):
            yield SimpleNamespace(id=1, text="Caption", photo=object(), document=None, date=datetime.now(timezone.utc))

    assert run_async(module.store_unsaved_messages(123, (SimpleNamespace(entity=None), None), FakeClient(), True,
                                                  show_download_status=False))
    assert module.request_media_description.await_count == int(enabled)
    description = "A cat" if enabled else None
    assert module.request_embeddings.await_args.args[1] == [f"Text: Caption\nMedia: {description}"]
    assert module.store_prepared_message.await_args.args[3] == description


def test_embedding_requests_have_no_fixed_character_limit(load_module, run_async, monkeypatch):
    module = load_module("database/messages.py")
    monkeypatch.setitem(module.user_info, 123, SimpleNamespace(history_size=100, process_images=False))
    module.get_saved_message_metadata = Mock(return_value={})
    module.store_prepared_message = AsyncMock()
    module.request_embeddings.side_effect = lambda user_id, texts, model: [object() for text in texts]

    class FakeClient:
        async def iter_messages(self, *args, **kwargs):
            for index in range(3):
                yield SimpleNamespace(id=index, text="x" * 40000, photo=None, date=datetime.now(timezone.utc))

    assert run_async(module.store_unsaved_messages(123, (SimpleNamespace(entity=None), None), FakeClient(), True,
                                                  show_download_status=False))
    assert [len(call.args[1]) for call in module.request_embeddings.await_args_list] == [3]


def test_host_reorders_embedding_response_by_input_index(load_module, run_async):
    module = load_module("host/llm/embeddings.py")
    module.llm_client.embeddings = SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(data=[
        SimpleNamespace(index=1, embedding=[0.2]), SimpleNamespace(index=0, embedding=[0.1]),
    ])))

    assert run_async(module.create_embeddings(["First", "Second"], "group-model")) == [[0.1], [0.2]]
    module.llm_client.embeddings.create.assert_awaited_once_with(model="group-model", input=["First", "Second"])


@pytest.mark.parametrize("status,message", [(400, "Maximum context length exceeded"),
                                          (413, "Payload too large"), (422, "Maximum batch size exceeded")])
def test_embedding_size_failure_splits_input_without_reordering_vectors(load_module, run_async, status, message):
    module = load_module("host/llm/embeddings.py")
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.test"))
    module.llm_client.embeddings = SimpleNamespace(create=AsyncMock(side_effect=[
        openai.APIStatusError(message, response=response, body=None),
        SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.1])]),
        SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.2])]),
    ]))

    assert run_async(module.create_embeddings(["First", "Second"], "group-model")) == [[0.1], [0.2]]
    assert [call.kwargs["input"] for call in module.llm_client.embeddings.create.await_args_list] == [
        ["First", "Second"], ["First"], ["Second"]]


@pytest.mark.parametrize("texts,message", [(["First", "Second"], "Invalid model configuration"),
                                          (["Too large"], "Maximum context length exceeded")])
def test_embedding_failure_does_not_retry_invalid_configuration_or_single_input(load_module, run_async, texts, message):
    module = load_module("host/llm/embeddings.py")
    response = httpx.Response(400, request=httpx.Request("POST", "https://example.test"))
    module.llm_client.embeddings = SimpleNamespace(create=AsyncMock(side_effect=
        openai.BadRequestError(message, response=response, body=None)))

    with pytest.raises(openai.BadRequestError):
        run_async(module.create_embeddings(texts, "group-model"))
    module.llm_client.embeddings.create.assert_awaited_once()


@pytest.mark.parametrize("indices", [[0], [0, 0], [0, 2]])
def test_host_rejects_incomplete_or_mismatched_embedding_batches(load_module, run_async, indices):
    module = load_module("host/llm/embeddings.py")
    module.llm_client.embeddings = SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(data=[
        SimpleNamespace(index=index, embedding=[0.1]) for index in indices
    ])))

    with pytest.raises(ValueError, match="incomplete or invalid batch"):
        run_async(module.create_embeddings(["First", "Second"], "group-model"))


@pytest.mark.parametrize("result", [None, [[0.1] * 1024], [[0.1], [0.2]], [[float("nan")] * 1024, [0.1] * 1024]])
def test_coordinator_rejects_invalid_embedding_batches(load_module, run_async, result):
    module = load_module("server/requests.py")
    module.send_request = AsyncMock(return_value=result)

    assert run_async(module.request_embeddings(123, ["First", "Second"], "group-model")) is None


def test_host_websocket_accepts_embedding_batch_and_checks_model(load_module, run_async):
    module = load_module("host/main.py")
    base = {"operation": "embedding", "user_id": 123, "data": ["First", "Second"]}
    incoming = [
        {"status": 200}, dict(base, request_id=1, embedding_model="group-model"),
        dict(base, request_id=2, embedding_model="unsupported"), dict(base, request_id=3, data=[123]),
    ]
    socket = SimpleNamespace(recv=AsyncMock(side_effect=[*map(json.dumps, incoming), EOFError()]), send=AsyncMock())
    module.create_embeddings.return_value = [[0.1], [0.2]]

    with pytest.raises(EOFError):
        run_async(module.keep_host_running(socket, ("group-model",)))

    module.create_embeddings.assert_awaited_once_with(["First", "Second"], "group-model")
    responses = [json.loads(call.args[0]) for call in socket.send.await_args_list]
    assert [response["status"] for response in responses] == [200, 404, 404]
    assert responses[0]["result"] == [[0.1], [0.2]]
