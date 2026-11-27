import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from utils.classes import UserState


@pytest.mark.parametrize("enabled", [False, True])
def test_new_image_description_respects_setting(load_module, run_async, monkeypatch, enabled):
    module = load_module("database/messages.py")
    monkeypatch.setitem(module.user_info, 123, SimpleNamespace(process_images=enabled))
    message = SimpleNamespace(photo=object(), document=None, id=1, get_sender=AsyncMock(return_value=None))
    module.store_private_message = AsyncMock()
    module.request_media_description.return_value = "Image description"
    monkeypatch.setattr(module.os.path, "exists", lambda path: True)

    assert run_async(module.store_message(message, (SimpleNamespace(entity=None), None), 123))

    assert module.request_media_description.await_count == int(enabled)
    assert module.store_private_message.await_args.args[4] == ("Image description" if enabled else None)


@pytest.mark.parametrize("text", ["01.11.2026 - 25.11.2026", "25.11.2026 - 25.11.2026"])
def test_date_interval_includes_the_whole_end_day(load_module, run_async, monkeypatch, text):
    module = load_module("features/search.py")
    user = SimpleNamespace(search_since=None, search_until=None, history_size=100, pending_search_query="When?")
    monkeypatch.setitem(module.user_info, 123, user)
    module.process_search_text = AsyncMock()

    run_async(module.process_search_dates(123, text))

    assert user.search_since.strftime("%d.%m.%Y") == text.split(" - ")[0]
    assert user.search_until == datetime(2026, 11, 26, tzinfo=timezone.utc)
    assert user.status == UserState.WAIT_FOR_REQUEST_ANSWER
    assert user.pending_search_query is None
    module.process_search_text.assert_awaited_once_with(123, "When?")


@pytest.mark.parametrize("text", ["31.02.2026 - 01.03.2026", "25.11.2026 - 01.11.2026", "1.11.2026 - 25.11.2026"])
def test_invalid_dates_preserve_previous_range(load_module, run_async, monkeypatch, text):
    module = load_module("features/search.py")
    user = SimpleNamespace(search_since="previous-start", search_until="previous-end", status=UserState.WAIT_FOR_SEARCH_DATES)
    monkeypatch.setitem(module.user_info, 123, user)

    run_async(module.process_search_dates(123, text))

    assert user.search_since == "previous-start" and user.search_until == "previous-end"
    assert user.status == UserState.WAIT_FOR_SEARCH_DATES
    assert "Invalid interval" in module.send_message.await_args.args[1]


@pytest.mark.parametrize("enabled,saved_description", [(False, None), (True, None), (True, "Saved description")])
def test_cached_images_are_processed_only_when_enabled_and_missing_description(load_module, run_async, monkeypatch, enabled, saved_description):
    module = load_module("database/messages.py")
    monkeypatch.setitem(module.user_info, 123, SimpleNamespace(process_images=enabled, history_size=100))
    module.get_saved_message_metadata = Mock(return_value={1: (saved_description, True)})
    module.update_media_description = Mock()
    module.request_media_description.return_value = "New description"
    module.request_embeddings.return_value = [[0.1]]
    message = SimpleNamespace(id=1, date=datetime.now(timezone.utc), photo=object(), document=None, text="Caption")

    class FakeClient:
        async def iter_messages(self, *args, **kwargs):
            yield message

    result = run_async(module.store_unsaved_messages(123, (SimpleNamespace(id=10, entity=None), None), FakeClient(), True,
                                                      show_download_status=False))

    assert result is True
    if enabled and saved_description is None:
        module.request_media_description.assert_awaited_once()
        module.update_media_description.assert_called_once()
        module.request_embeddings.assert_awaited_once_with(123, ["Text: Caption\nMedia: New description"], "test-embedding")
    else:
        module.request_media_description.assert_not_awaited()
        module.request_embeddings.assert_not_awaited()


def test_downloader_skips_messages_outside_custom_interval(load_module, run_async, monkeypatch):
    module = load_module("database/messages.py")
    monkeypatch.setitem(module.user_info, 123, SimpleNamespace(process_images=False))
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    end = datetime(2026, 11, 26, tzinfo=timezone.utc)
    module.get_saved_message_metadata = Mock(return_value={})
    module.store_prepared_message = AsyncMock()
    module.request_embeddings.side_effect = lambda user_id, texts, model: [[0.1] for text in texts]

    class FakeClient:
        async def iter_messages(self, *args, **kwargs):
            assert kwargs["offset_date"] == end
            for index, date in enumerate([end, end - timedelta(seconds=1), start, start - timedelta(seconds=1)]):
                yield SimpleNamespace(id=index, date=date, photo=None, text=str(index))

    assert run_async(module.store_unsaved_messages(123, (SimpleNamespace(entity=None), None), FakeClient(), True,
                                                   since=start, until=end, show_download_status=False))
    assert [call.args[0].id for call in module.store_prepared_message.await_args_list] == [1, 2]


@pytest.mark.parametrize("outcome", ["success", "no_embedding", "error", "cancelled"])
def test_search_preparation_animates_while_waiting_and_stops_afterwards(load_module, run_async, monkeypatch, outcome):
    module = load_module("features/search.py")
    user = SimpleNamespace(client=object(), dialogs=[(SimpleNamespace(entity=None), None)],
                           search_in_progress=False, history_size=100, search_since=None, search_until=None)
    monkeypatch.setitem(module.user_info, 123, user)
    module.select_host.return_value = (object(), "test-embedding")
    module.get_best_private_messages.return_value = [object()]
    module.get_message_info.side_effect = lambda data, message: data.append({"type": "text", "text": "Saved message"})
    status_message = SimpleNamespace(delete=AsyncMock())
    real_sleep = asyncio.sleep
    animated_texts = []

    async def fast_sleep(seconds):
        assert seconds == 3
        await real_sleep(0)

    monkeypatch.setattr(module.asyncio, "sleep", fast_sleep)

    async def flow():
        animated = asyncio.Event()

        async def send_message(user_id, text, **kwargs):
            if "░" in text or "█" in text:
                assert kwargs["message"] is status_message
                assert kwargs["edit"] is True
                animated_texts.append(text)
                if len(animated_texts) >= 12:
                    animated.set()
            return status_message

        async def request_embedding(*args):
            await animated.wait()
            if outcome == "cancelled":
                raise asyncio.CancelledError
            if outcome == "error":
                raise module.DatabaseError("Connection lost")
            return None if outcome == "no_embedding" else [0.1]

        async def model_answer(*args, **kwargs):
            assert not any(task.get_coro().__name__ == "animate_preparation" for task in asyncio.all_tasks())
            return "Answer"

        module.send_message.side_effect = send_message
        module.request_embedding.side_effect = request_embedding
        module.send_request.side_effect = model_answer
        if outcome == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await module.process_search_text(123, "When?")
        else:
            await module.process_search_text(123, "When?")
        count = len(animated_texts)
        await real_sleep(0)
        assert len(animated_texts) == count
        assert not user.search_in_progress

    run_async(flow())

    bars = [text.splitlines()[1] for text in animated_texts]
    assert bars[:11] == ["█" * count + "░" * (40 - count) for count in range(0, 41, 4)]
    assert bars[11] == "░" * 40
    status_message.delete.assert_awaited_once()
    if outcome == "success":
        module.send_request.assert_awaited_once()
    else:
        module.send_request.assert_not_awaited()


@pytest.mark.parametrize("html_accepted", [True, False])
def test_search_formats_html_and_falls_back_to_plain_text(load_module, run_async, monkeypatch, html_accepted):
    module = load_module("features/search.py")
    user = SimpleNamespace(client=object(), dialogs=[(SimpleNamespace(entity=None), None)],
                           search_in_progress=False, history_size=100, search_since=None, search_until=None)
    monkeypatch.setitem(module.user_info, 123, user)
    module.select_host.return_value = (object(), "test-embedding")
    module.request_embedding.return_value = [0.1]
    module.get_best_private_messages.return_value = [object()]
    module.get_message_info.side_effect = lambda data, message: data.append({"type": "text", "text": "A message"})
    module.send_request.return_value = "<b>No test.</b>\n<blockquote>Thursday &amp; Friday</blockquote>"
    status_message = SimpleNamespace(delete=AsyncMock())

    async def send_message(user_id, text, **kwargs):
        return None if kwargs.get("parse_mode") == "HTML" and not html_accepted else status_message

    module.send_message.side_effect = send_message

    run_async(module.process_search_text(123, "When?"))

    html_calls = [call for call in module.send_message.await_args_list if call.kwargs.get("parse_mode") == "HTML"]
    assert len(html_calls) == 1
    assert html_calls[0].args[1] == module.send_request.return_value
    if not html_accepted:
        assert module.send_message.await_args.args == (123, "No test.\nThursday & Friday")
        assert "parse_mode" not in module.send_message.await_args.kwargs
