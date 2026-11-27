import asyncio
from types import SimpleNamespace
import sys
from unittest.mock import AsyncMock, Mock

import pytest

from utils.classes import AppUser, UserState


def make_user():
    user = AppUser(user_id=123, status=UserState.WAIT_FOR_SEARCH_TEXT, last_activity=0,
                   password_hash="saved-password-hash", string_session="encrypted-session", token_hash="saved-token",
                   pending_search_query="Old query", pairing_code_hash="pairing-code", pairing_code_expires_at=9999)
    user.client = SimpleNamespace(disconnect=AsyncMock())
    return user


@pytest.mark.parametrize("elapsed,expired", [(1799, False), (1800, True), (1801, True)])
def test_session_expires_after_thirty_minutes_and_retains_account_data(load_module, run_async, monkeypatch, elapsed, expired):
    module = load_module("utils/session.py")
    user = make_user()
    client = user.client
    monkeypatch.setattr(module, "user_info", {123: user})
    monkeypatch.setattr(module, "monotonic", lambda: elapsed)

    assert run_async(module.expire_idle_session(123)) is expired

    assert user.password_hash == "saved-password-hash"
    assert user.string_session == "encrypted-session"
    assert user.token_hash == "saved-token"
    if expired:
        assert user.status == UserState.LOGGED_OUT
        assert user.client is None
        assert user.pending_search_query is None
        assert user.pairing_code_hash is None
        client.disconnect.assert_awaited_once()
        module.reset_idle_timer.assert_called_once_with(123, False)
        module.disable_inline_keyboard.assert_awaited_once_with(123)
        assert "enter your bot password" in module.send_message.await_args.args[1]
        assert run_async(module.expire_idle_session(123)) is False
        module.send_message.assert_awaited_once()
    else:
        assert user.client is client
        client.disconnect.assert_not_awaited()
        module.send_message.assert_not_awaited()


@pytest.mark.parametrize("host_id,disconnected,expired", [(123, False, False), (123, True, True), (456, False, True)])
def test_only_connected_own_host_keeps_session_open(load_module, run_async, monkeypatch, host_id, disconnected, expired):
    module = load_module("utils/session.py")
    user = make_user()
    monkeypatch.setattr(module, "user_info", {123: user})
    monkeypatch.setattr(module, "available_hosts", {host_id: SimpleNamespace(disconnected=disconnected)})
    monkeypatch.setattr(module, "monotonic", lambda: 10000)

    assert run_async(module.expire_idle_session(123)) is expired


def test_authentication_records_activity_and_rejects_expired_session(load_module, run_async, monkeypatch):
    session = load_module("utils/session.py")
    checks = load_module("utils/check_authentication.py")
    user = make_user()
    for module in (session, checks):
        monkeypatch.setattr(module, "user_info", {123: user})
    monkeypatch.setattr(checks, "uninitialized_users", {})
    checks.expire_idle_session = session.expire_idle_session
    checks.record_activity = session.record_activity
    current_time = Mock(return_value=1000)
    monkeypatch.setattr(session, "monotonic", current_time)

    assert run_async(checks.check_authentication(123)) is True
    assert user.last_activity == 1000
    current_time.return_value = 2800
    assert run_async(checks.check_authentication(123)) is False
    assert user.status == UserState.LOGGED_OUT


def test_disconnecting_own_host_starts_fresh_idle_period(load_module, run_async, monkeypatch):
    session = load_module("utils/session.py")
    server = load_module("server/__init__.py")
    user = make_user()
    host = SimpleNamespace(user=user, disconnected=False)
    server.available_hosts[123] = host
    monkeypatch.setattr(session, "available_hosts", server.available_hosts)
    monkeypatch.setattr(session, "user_info", {123: user})
    monkeypatch.setattr(sys.modules["utils.session"], "record_activity", session.record_activity)
    current_time = Mock(return_value=10000)
    monkeypatch.setattr(session, "monotonic", current_time)

    run_async(server.disconnect_host(host))

    assert user.last_activity == 10000
    assert run_async(session.expire_idle_session(123)) is False
    current_time.return_value = 11800
    assert run_async(session.expire_idle_session(123)) is True


def test_replaced_host_cleanup_does_not_reset_session_timer(load_module, run_async, monkeypatch):
    server = load_module("server/__init__.py")
    user = make_user()
    old_host = SimpleNamespace(user=user, disconnected=False)
    new_host = SimpleNamespace(user=user, disconnected=False)
    server.available_hosts[123] = new_host
    recorder = sys.modules["utils.session"].record_activity

    run_async(server.disconnect_host(old_host))

    assert server.available_hosts[123] is new_host
    recorder.assert_not_called()


@pytest.mark.parametrize("status,search_in_progress", [(UserState.WAIT_FOR_REQUEST_ANSWER, False),
                                                      (UserState.WAIT_FOR_SEARCH_TEXT, True)])
def test_foreground_processing_can_finish_before_session_expires(load_module, run_async, monkeypatch, status, search_in_progress):
    module = load_module("utils/session.py")
    user = make_user()
    user.status = status
    user.search_in_progress = search_in_progress
    monkeypatch.setattr(module, "user_info", {123: user})
    monkeypatch.setattr(module, "monotonic", lambda: 10000)

    assert run_async(module.expire_idle_session(123)) is False
    assert user.last_activity == 10000
    user.client.disconnect.assert_not_awaited()


def test_background_preloading_does_not_keep_session_open(load_module, run_async, monkeypatch):
    module = load_module("utils/session.py")
    user = make_user()
    user.preloading = True
    monkeypatch.setattr(module, "user_info", {123: user})
    monkeypatch.setattr(module, "monotonic", lambda: 1800)

    assert run_async(module.expire_idle_session(123)) is True
    module.reset_idle_timer.assert_called_once_with(123, False)


def test_watcher_checks_all_users_even_if_one_check_fails(load_module, run_async, monkeypatch):
    module = load_module("utils/session.py")
    monkeypatch.setattr(module, "user_info", {123: object(), 456: object()})
    module.expire_idle_session = AsyncMock(side_effect=[RuntimeError("Failed"), True])
    sleep = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    monkeypatch.setattr(module.asyncio, "sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        run_async(module.watch_idle_sessions())

    assert [call.args for call in module.expire_idle_session.await_args_list] == [(123,), (456,)]
    assert all(call.args == (30,) for call in sleep.await_args_list)
