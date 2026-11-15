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

    if public:
        module.get_best_public_messages(dialog, vector, 123, "group-model")
        expected_scope = (20, 30)
        table = "public_message_model_embedding"
    else:
        module.get_best_private_messages(123, dialog, vector, "group-model")
        expected_scope = (10, 123, 30)
        table = "private_message_model_embedding"

    query, params = cursor.execute.call_args.args
    assert table in query
    assert query.index("e.model = %s") < query.index("ORDER BY e.embedding")
    assert params[:len(expected_scope) + 2] == ("group-model", *expected_scope, vector)


def test_search_keeps_one_model_for_query_messages_and_answer(load_module, run_async, monkeypatch):
    module = load_module("features/search.py")
    dialog = (SimpleNamespace(id=10, entity=None), None)
    monkeypatch.setitem(module.user_info, 123, SimpleNamespace(client=object(), dialogs=[dialog]))
    module.select_host.return_value = (SimpleNamespace(embedding_models=("first-model", "group-model")), "group-model")
    module.request_embedding.return_value = [0.1]

    run_async(module.process_search_text(123, "When?"))

    module.request_embedding.assert_awaited_once_with(123, "When?", "group-model")
    assert module.store_unsaved_messages.call_args.kwargs["embedding_model"] == "group-model"
    module.get_best_private_messages.assert_called_once_with(123, dialog, [0.1], "group-model")
    module.send_request.assert_awaited_once_with(123, [], "search", "When?", embedding_model="group-model")
