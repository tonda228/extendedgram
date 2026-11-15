import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.websockets import WebSocket
from telegram.error import RetryAfter
from websockets.exceptions import ConnectionClosedOK
from pydantic import ValidationError
from telethon.tl.types import ForumTopic
from telethon.tl.types import User

from utils.classes import AppUser, Host, UserState


def test_cancelled_request_releases_count_and_future(load_module, run_async, monkeypatch):
    module = load_module('server/requests.py')
    websocket = WebSocket({'type': 'websocket'}, AsyncMock(), AsyncMock())
    host = Host(user=AppUser(user_id=123, status=UserState.AUTHENTICATED), websocket=websocket)
    module.get_host.return_value = host
    monkeypatch.setattr(websocket, 'send_json', AsyncMock(side_effect=asyncio.CancelledError()))

    with pytest.raises(asyncio.CancelledError):
        run_async(module.send_request(123, 'text', 'embedding'))

    assert host.requests_count == 0
    assert module.active_requests == {}
    module.disconnect_host.assert_awaited_once_with(host)
    module.close_websocket.assert_awaited_once_with(host, notify=False)


def test_disconnecting_old_host_preserves_replacement(load_module, run_async):
    module = load_module('server/__init__.py')
    old = SimpleNamespace(user=SimpleNamespace(user_id=123))
    new = SimpleNamespace(user=SimpleNamespace(user_id=123))
    module.available_hosts[123] = new
    module.available_group_hosts[1] = [old]
    module.get_app_user_groups.side_effect = AssertionError('Cleanup must not query memberships')

    run_async(module.disconnect_host(old))

    assert module.available_hosts[123] is new
    assert module.available_group_hosts[1] == []
    module.update_host_priorities(old)
    assert module.available_group_hosts[1] == []


@pytest.mark.parametrize('delay', [12, timedelta(seconds=12)])
def test_progress_waits_for_flood_delay(load_module, run_async, monkeypatch, delay):
    module = load_module('utils/helpers.py')
    message = SimpleNamespace(message_id=42)
    edit = AsyncMock(side_effect=[RetryAfter(delay), message])
    monkeypatch.setattr(module.application.bot, 'edit_message_text', edit, raising=False)
    monkeypatch.setattr(module, 'user_info', {})
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, 'sleep', sleep)

    assert run_async(module.resend_processing_status(123, message, 50, 100)) is True

    sleep.assert_awaited_once_with(12)
    assert edit.await_count == 2
    assert edit.call_args.kwargs['message_id'] == 42
    assert edit.call_args.kwargs['text'] == '█' * 20 + '░' * 20 + ' 50%'


def test_zero_progress_total_does_not_send(load_module, run_async):
    module = load_module('utils/helpers.py')
    module.send_message = AsyncMock()

    assert run_async(module.resend_processing_status(123, None, 0, 0)) is False
    module.send_message.assert_not_awaited()


def test_long_plain_reply_is_split(load_module, run_async):
    module = load_module('utils/helpers.py')
    text = "😀" * 3000 + " source"

    run_async(module.send_message(123, text))

    calls = module.application.bot.send_message.await_args_list
    assert len(calls) > 1
    chunks = [call.args[1] for call in calls]
    assert all(len(chunk.encode('utf-16-le')) // 2 <= 4096 for chunk in chunks)
    assert ''.join(chunks) == text
    assert all(call.kwargs['parse_mode'] is None for call in calls)


def test_initial_host_disconnect_is_handled(load_module, run_async, monkeypatch):
    module = load_module('host/main.py')
    async def wait_closed():
        await asyncio.Event().wait()

    socket = SimpleNamespace(recv=AsyncMock(side_effect=ConnectionClosedOK(None, None)),
                             close=AsyncMock(), wait_closed=wait_closed, close_code=1000)
    monkeypatch.setattr(module.websockets, 'connect', AsyncMock(return_value=socket))

    run_async(module.open_connection({'user_id': 123, 'token': 'fake', 'password': 'fake'}))

    socket.close.assert_awaited_once()


def test_host_skips_malformed_json_and_rejects_missing_data(load_module, run_async):
    module = load_module('host/main.py')
    socket = SimpleNamespace(
        recv=AsyncMock(side_effect=[
            '{"status": 200}', '{', '[]',
            '{"request_id": 42, "user_id": 123}', ConnectionClosedOK(None, None),
        ]),
        send=AsyncMock(),
    )

    with pytest.raises(ConnectionClosedOK):
        run_async(module.keep_host_running(socket))

    socket.send.assert_awaited_once()
    assert '42' in socket.send.call_args.args[0]


def test_repairing_account_replaces_saved_credentials(load_module, monkeypatch, tmp_path):
    module = load_module('host/main.py')
    monkeypatch.chdir(tmp_path)
    accounts = [{'user_id': 123, 'token': 'old', 'password': 'old'}]

    module.store_new_users(accounts, [(123, 'new', 'password'), (456, 'other', 'password')])

    assert [account['user_id'] for account in accounts] == [123, 456]
    assert accounts[0]['token'] == 'new'


def test_failed_new_embedding_is_reported(load_module, run_async):
    module = load_module('database/messages.py')
    module.request_message_embedding.return_value = None
    message = SimpleNamespace(photo=None)

    assert run_async(module.store_message(message, (None, None), 123, True)) is False
    module.connection.commit.assert_not_called()


def test_download_throttles_progress_and_cleans_message(load_module, run_async, monkeypatch):
    module = load_module('database/messages.py')
    monkeypatch.setattr(module, 'user_info', {123: SimpleNamespace(history_size=10)})
    monkeypatch.setattr(module, 'monotonic', Mock(return_value=0))
    module.get_private_messages = Mock(return_value=[])
    module.store_message = AsyncMock(return_value=True)
    progress = SimpleNamespace(delete=AsyncMock())
    module.send_message.side_effect = [SimpleNamespace(message_id=1), progress, None]
    messages = [SimpleNamespace(id=index, date=datetime.now(timezone.utc)) for index in [3, 2, 1]]

    class FakeClient:
        async def iter_messages(self, *args, **kwargs):
            for message in messages:
                yield message

    dialog = (SimpleNamespace(entity=None), None)
    assert run_async(module.store_unsaved_messages(123, dialog, FakeClient(), True, 3)) is True
    module.resend_processing_status.assert_not_awaited()
    progress.delete.assert_awaited_once()


def test_summary_download_fills_gaps_below_newest_cached_message(load_module, run_async, monkeypatch):
    module = load_module('database/messages.py')
    monkeypatch.setattr(module, 'user_info', {123: SimpleNamespace(history_size=10)})
    module.get_private_messages = Mock(return_value=[SimpleNamespace(message_id=105)])
    module.send_message.return_value = None
    module.store_message = AsyncMock(return_value=True)
    module.get_unread_count.return_value = 3

    class FakeClient:
        async def iter_messages(self, *args, **kwargs):
            assert kwargs['limit'] == 3
            for message_id in [105, 104, 103]:
                yield SimpleNamespace(id=message_id, date=datetime.now(timezone.utc))

    result = run_async(module.store_unsaved_messages(123, (SimpleNamespace(entity=None), None), FakeClient()))

    assert result is True
    assert [call.args[0].id for call in module.store_message.await_args_list] == [104, 103]


def test_new_message_keeps_manual_dialog_permission(load_module, run_async, monkeypatch):
    module = load_module('features/event_handlers.py')
    handlers = []

    class FakeClient:
        def on(self, event_type):
            def register(handler):
                handlers.append(handler)
                return handler
            return register

    user = SimpleNamespace(client=FakeClient(), preloading=True, allow_all=False,
                           allowed_dialogs_set={(456, 0)})
    monkeypatch.setattr(module, 'user_info', {123: user})
    module.get_topic = AsyncMock(return_value=None)
    dialog = User(id=456, first_name='Friend')
    event = SimpleNamespace(get_chat=AsyncMock(return_value=dialog), message=SimpleNamespace())

    run_async(module.create_new_message_handler(123))
    run_async(handlers[0](event))

    module.store_dialog.assert_called_once_with((dialog, None), 123, is_allowed=True, update=False)
    module.store_message.assert_awaited_once()


@pytest.mark.parametrize('values', [[0.1, 0.2], [float('nan')] * 1024])
def test_incompatible_embedding_is_rejected(load_module, run_async, values):
    module = load_module('server/requests.py')
    module.send_request = AsyncMock(return_value=values)

    assert run_async(module.request_embedding(123, 'question')) is None


def test_restored_user_is_prompted_for_password(load_module, run_async, monkeypatch):
    module = load_module('utils/check_authentication.py')
    import utils.auth as auth
    prompt = AsyncMock()
    monkeypatch.setattr(auth, 'query_bot_password', prompt)
    user = AppUser(user_id=123, status=UserState.LOGGED_OUT)
    monkeypatch.setattr(module, 'user_info', {123: user})
    monkeypatch.setattr(module, 'uninitialized_users', {})

    assert run_async(module.check_authentication(123)) is False
    prompt.assert_awaited_once_with(user)


def test_summary_rejects_missing_message_ids(load_module, run_async):
    module = load_module('host/summarize.py')
    module.send_data.return_value = [{'topic': 'Meeting', 'summary': 'Friday'}]

    with pytest.raises(ValidationError):
        run_async(module.process_summarize_request([]))


def test_logout_removes_deleted_group_heaps_after_account_deletion(load_module, run_async, monkeypatch):
    module = load_module('bot/commands/logout.py')
    user = AppUser(user_id=123, status=UserState.AUTHENTICATED)
    monkeypatch.setattr(module, 'user_info', {123: user})
    monkeypatch.setattr(module, 'user_preloading', {})
    module.available_group_hosts[1] = [SimpleNamespace()]
    module.get_affected_users_and_groups.return_value = {456: [(1, 'Friends')]}
    order = []
    module.delete_app_user.side_effect = lambda user_id, **kwargs: order.append('account')
    module.delete_unused_channels.side_effect = lambda **kwargs: order.append('channels')
    module.delete_unused_users.side_effect = lambda **kwargs: order.append('users')

    run_async(module.log_out_confirmation(123))

    assert order == ['account', 'channels', 'users']
    assert module.user_info == {}
    assert 1 not in module.available_group_hosts
    module.update_user_groups.assert_awaited_once_with({456: [(1, 'Friends')]}, 123)


def test_failed_account_deletion_preserves_runtime_user(load_module, run_async, monkeypatch):
    module = load_module('bot/commands/logout.py')
    user = AppUser(user_id=123, status=UserState.AUTHENTICATED)
    monkeypatch.setattr(module, 'user_info', {123: user})
    monkeypatch.setattr(module, 'user_preloading', {})
    module.delete_app_user.side_effect = RuntimeError('database unavailable')

    with pytest.raises(RuntimeError):
        run_async(module.log_out_confirmation(123))

    assert module.user_info[123] is user
    module.connection.rollback.assert_called_once()


def test_forum_discovery_fetches_next_page(load_module, run_async, monkeypatch):
    module = load_module('database/dialogs.py')
    first = Mock(spec=ForumTopic, id=1, top_message=10, date=datetime.now(timezone.utc))
    second = Mock(spec=ForumTopic, id=2, top_message=20, date=datetime.now(timezone.utc))
    pages = [SimpleNamespace(topics=[first], count=2, order_by_create_date=True),
             SimpleNamespace(topics=[second], count=2, order_by_create_date=True)]
    dialog = SimpleNamespace(is_group=True, entity=SimpleNamespace(forum=True))

    class FakeClient:
        def __init__(self):
            self.requests = []

        async def iter_dialogs(self):
            yield dialog

        async def __call__(self, request):
            self.requests.append(request)
            return pages.pop(0)

    client = FakeClient()
    monkeypatch.setattr(module, 'user_info', {123: SimpleNamespace(client=client)})
    module.sync_dialogs = Mock()

    result = run_async(module.get_all_dialogs(123))

    assert [topic.id for _, topic in result] == [1, 2]
    assert client.requests[1].offset_topic == 1
    assert client.requests[1].offset_id == 10


def test_invalid_host_model_is_rejected_before_accept(load_module, run_async):
    module = load_module('server/server_runner.py')
    socket = SimpleNamespace(headers={'embedding-model': 'invalid model'}, close=AsyncMock(), accept=AsyncMock())

    run_async(module.register_new_host(socket, 123))

    socket.accept.assert_not_awaited()
    assert socket.close.call_args.kwargs['code'] == 1008


@pytest.mark.parametrize('count', [1, 100, 101])
def test_priority_history_keeps_latest_hundred(load_module, count):
    module = load_module('database/dialogs.py')
    module.delete_old_dialog_priorities(123)
    query, parameters = module.cur.execute.call_args.args
    # Run the portable deletion query against a temporary in-memory database.
    with sqlite3.connect(':memory:') as database:
        database.execute('CREATE TABLE dialog_priority (user_id INT, dialog_id INT, topic_id INT, date_time INT)')
        database.executemany('INSERT INTO dialog_priority VALUES (?, ?, ?, ?)',
                             [(123, 1, 0, index) for index in range(count)] + [(456, 1, 0, 0)])
        database.execute(query.replace('%s', '?'), parameters)

        remaining = database.execute('SELECT date_time FROM dialog_priority WHERE user_id = 123 ORDER BY date_time').fetchall()
        assert remaining == [(index,) for index in range(max(0, count - 100), count)]
        assert database.execute('SELECT COUNT(*) FROM dialog_priority WHERE user_id = 456').fetchone()[0] == 1
