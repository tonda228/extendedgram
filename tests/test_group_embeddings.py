from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize("public", [True, False])
def test_search_filters_model_before_comparing_vectors(load_module, public):
    module = load_module("database/messages.py")
    cursor = MagicMock()
    module.connection.cursor.return_value = MagicMock()
    module.connection.cursor.return_value.__enter__.return_value = cursor
    dialog = (SimpleNamespace(id=10, entity=SimpleNamespace(id=20)), SimpleNamespace(id=30))
    vector = [0.1] * 1024
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)

    if public:
        module.get_best_public_messages(dialog, vector, 123, "group-model", since=since, until=None)
        expected_scope = (20, 30)
        table = "public_message_model_embedding"
    else:
        module.get_best_private_messages(123, dialog, vector, "group-model", since=since, until=None)
        expected_scope = (10, 123, 30)
        table = "private_message_model_embedding"

    query, params = cursor.execute.call_args.args
    assert table in query
    assert query.index("e.model = %s") < query.index("ORDER BY e.embedding")
    assert params[:len(expected_scope) + 4] == ("group-model", *expected_scope, since, datetime.max.replace(tzinfo=timezone.utc), vector)
    assert params.count(since) == 2
    assert query.count("%s") == len(params)


def test_search_keeps_one_model_for_query_messages_and_answer(load_module, run_async, monkeypatch):
    module = load_module("features/search.py")
    dialog = (SimpleNamespace(id=10, entity=None), None)
    monkeypatch.setitem(module.user_info, 123, SimpleNamespace(client=object(), dialogs=[dialog], search_in_progress=False,
                                                             history_size=100, search_since=None, search_until=None))
    module.select_host.return_value = (SimpleNamespace(embedding_models=("first-model", "group-model")), "group-model")
    module.request_embedding.return_value = [0.1]
    module.get_best_private_messages.return_value = [object()]
    item = {"type": "text", "text": "Saved message"}
    module.get_message_info.side_effect = lambda data, message: data.append(item)

    run_async(module.process_search_text(123, "When?"))

    module.request_embedding.assert_awaited_once_with(123, "When?", "group-model")
    assert module.store_unsaved_messages.call_args.kwargs["embedding_model"] == "group-model"
    since = module.store_unsaved_messages.await_args.kwargs["since"]
    module.get_best_private_messages.assert_called_once_with(123, dialog, [0.1], "group-model", since=since, until=None)
    module.send_request.assert_awaited_once_with(123, [item], "search", "When?", embedding_model="group-model")


def test_empty_search_skips_model_and_uses_same_history_period_across_chats(load_module, run_async, monkeypatch):
    module = load_module("features/search.py")
    dialogs = [(SimpleNamespace(id=index, entity=None), None) for index in range(4)]
    user = SimpleNamespace(client=object(), dialogs=dialogs, search_in_progress=False, history_size=12, search_since=None, search_until=None)
    monkeypatch.setitem(module.user_info, 123, user)
    module.select_host.return_value = (object(), "group-model")
    module.request_embedding.return_value = [0.1]

    run_async(module.process_search_text(123, "When?"))

    calls = module.store_unsaved_messages.await_args_list
    assert len(calls) == len(dialogs)
    cutoffs = {call.kwargs["since"] for call in calls}
    assert len(cutoffs) == 1
    elapsed = datetime.now(timezone.utc) - cutoffs.pop()
    assert elapsed.days == 12
    module.send_request.assert_not_awaited()
    assert "No indexed messages" in module.menu.await_args.kwargs["notice"]
    assert not user.search_in_progress
