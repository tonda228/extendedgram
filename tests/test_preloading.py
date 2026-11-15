from types import SimpleNamespace
from unittest.mock import Mock

from utils.classes import AppUser, UserState


def test_preloading_downloads_each_recent_dialog(load_module, run_async, monkeypatch):
    module = load_module("features/preloading.py")
    client = Mock()
    monkeypatch.setitem(module.user_info, 123, SimpleNamespace(client=client))
    module.get_recent_dialogs.return_value = ["first-dialog", "second-dialog"]

    run_async(module.start_preloading(123))

    calls = module.store_unsaved_messages.await_args_list
    assert [call.args[1] for call in calls] == ["first-dialog", "second-dialog"]
    assert all(call.kwargs["add_embeddings"] is True for call in calls)


def test_reset_cancels_tasks_when_operation_starts(load_module, monkeypatch):
    module = load_module("features/preloading.py")
    monkeypatch.setitem(module.user_info, 123, AppUser(user_id=123, status=UserState.AUTHENTICATED, preloading=True))
    idle_task = Mock()
    download_task = Mock()
    state = SimpleNamespace(idle_task=idle_task, preloading=download_task)
    monkeypatch.setitem(module.user_preloading, 123, state)

    module.reset_idle_timer(123, reset=False)

    idle_task.cancel.assert_called_once()
    download_task.cancel.assert_called_once()
    assert state.preloading is None


def test_disabled_preloading_does_not_download(load_module, run_async, monkeypatch):
    module = load_module("features/preloading.py")
    monkeypatch.setitem(module.user_info, 123, AppUser(user_id=123, status=UserState.AUTHENTICATED, preloading=False))
    monkeypatch.setitem(module.user_preloading, 123, SimpleNamespace())

    run_async(module.wait_until_idle(123))

    module.get_recent_dialogs.assert_not_awaited()
