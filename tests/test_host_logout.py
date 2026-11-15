import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from utils.classes import AppUser, UserState


@pytest.mark.parametrize("is_host", [True, False])
def test_preloading_button_is_only_shown_for_hosts(load_module, run_async, monkeypatch, is_host):
    module = load_module("settings/__init__.py")
    user = AppUser(user_id=123, status=UserState.AUTHENTICATED, is_host=is_host)
    monkeypatch.setattr(module, "user_info", {123: user})

    run_async(module.settings(user_id=123))

    buttons = module.send_message.call_args.kwargs["reply_markup"].inline_keyboard
    actions = [button.callback_data for row in buttons for button in row]
    assert ("preloading" in actions) is is_host
    assert "set_read_after_summary" in actions


@pytest.mark.parametrize("close_code", [4001, 1000, 1006])
def test_host_removes_only_logged_out_account(load_module, run_async, monkeypatch, tmp_path, close_code):
    module = load_module("host/main.py")
    monkeypatch.chdir(tmp_path)
    accounts = [
        {"user_id": 123, "token": "first", "password": "first"},
        {"user_id": 456, "token": "second", "password": "second"},
    ]
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps(accounts), encoding="utf-8")

    async def process(socket, embedding_models):
        # Simulate a model request still running when the socket closes.
        await asyncio.Event().wait()

    socket = SimpleNamespace(wait_closed=AsyncMock(), close_code=close_code, close=AsyncMock())
    module.keep_host_running = AsyncMock(side_effect=process)
    monkeypatch.setattr(module.websockets, "connect", AsyncMock(return_value=socket))

    run_async(module.open_connection(accounts[0]))

    saved = json.loads(auth_file.read_text(encoding="utf-8"))
    assert saved == (accounts[1:] if close_code == 4001 else accounts)
    socket.close.assert_awaited_once()


@pytest.mark.parametrize("database_failure", [True, False])
def test_logout_notifies_host_only_after_database_commit(load_module, run_async, monkeypatch, database_failure):
    module = load_module("bot/commands/logout.py")
    user = AppUser(user_id=123, is_host=True, status=UserState.AUTHENTICATED)
    host = SimpleNamespace(user=user)
    monkeypatch.setattr(module, "user_info", {123: user})
    monkeypatch.setattr(module, "user_preloading", {})
    module.available_hosts[123] = host

    if database_failure:
        module.connection.commit.side_effect = RuntimeError("Database unavailable")
        with pytest.raises(RuntimeError):
            run_async(module.log_out_confirmation(123))
        module.close_websocket.assert_not_awaited()
    else:
        async def close(*args, **kwargs):
            module.connection.commit.assert_called_once()

        module.close_websocket.side_effect = close
        run_async(module.log_out_confirmation(123))
        module.close_websocket.assert_awaited_once_with(
            host, notify=False, code=4001, reason="Account logged out")


def test_revoked_account_gets_logout_code_on_reconnect(load_module, run_async):
    module = load_module("server/server_runner.py")
    module.authenticate_host = Mock(return_value=None)
    socket = SimpleNamespace(
        headers={"authorization": "Bearer old-token", "password": "old-password", "embedding-model": "test-embedding"},
        accept=AsyncMock(), close=AsyncMock())

    run_async(module.register_new_host(socket, 123))

    socket.close.assert_awaited_once_with(code=4001, reason="Account credentials revoked")
    assert not module.available_hosts


def test_removing_account_is_safe_when_auth_file_is_missing(load_module, monkeypatch, tmp_path):
    module = load_module("host/main.py")
    monkeypatch.chdir(tmp_path)

    module.remove_saved_user(123)

    assert not (tmp_path / "auth.json").exists()
