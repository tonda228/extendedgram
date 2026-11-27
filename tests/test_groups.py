import sys
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from telegram import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.error import Forbidden

from utils.classes import AppUser, UserState, UserGroupDBObject


def test_group_creation_keeps_prompts_and_retires_their_buttons(load_module, run_async, monkeypatch):
    groups = load_module("database/groups.py")
    helpers = load_module("utils/helpers.py")
    menu = load_module("bot/commands/menu.py")
    monkeypatch.setitem(sys.modules, "bot.commands.menu", menu)
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("Groups", callback_data="groups")]])
    old_menu = Message(message_id=1, chat=Chat(123, "private"), date=datetime.now(timezone.utc),
                       text="Settings", reply_markup=markup)
    user = AppUser(user_id=123, status=UserState.AUTHENTICATED, groups=[], last_message=old_menu)
    for module in (groups, helpers, menu):
        monkeypatch.setattr(module, "user_info", {123: user})
    groups.send_message = menu.send_message = helpers.send_message
    sent = []

    async def send(chat_id, text, **kwargs):
        message = Message(message_id=10 + len(sent), chat=Chat(chat_id, "private"),
                          date=datetime.now(timezone.utc), text=text, reply_markup=kwargs.get("reply_markup"))
        sent.append(message)
        return message

    helpers.application.bot.send_message.side_effect = send
    group = UserGroupDBObject(group_id=1, name="Friends", password_hash="hash", created_by=123)
    cursor = MagicMock()
    cursor.execute.return_value = cursor
    cursor.fetchone.side_effect = [None, group]
    groups.connection.cursor.return_value = MagicMock()
    groups.connection.cursor.return_value.__enter__.return_value = cursor

    async def create():
        await groups.create_group_request(123)
        await groups.query_new_group_password(123, "31")
        await groups.query_new_group_password(123, "Friends")
        await groups.query_password_confirmation(123, "password")
        await groups.process_password_confirmation(123, "password")
        await groups.save_group_embedding_model(123, "default")

    run_async(create())

    prompts = [message for message in sent[:-1] if message.reply_markup is not None]
    assert len(prompts) == 4
    assert "group name" in prompts[0].text
    assert "password for your group" in prompts[1].text
    assert prompts[2].text == "Confirm password."
    assert "Server default: test-embedding" in prompts[3].text
    assert "does not install it" in prompts[3].text
    helpers.application.bot.delete_message.assert_awaited_once_with(chat_id=123, message_id=old_menu.message_id)
    retired = [call.kwargs["message_id"] for call in helpers.application.bot.edit_message_reply_markup.await_args_list]
    assert retired == [message.message_id for message in prompts]
    assert all(call.kwargs["reply_markup"] is None
               for call in helpers.application.bot.edit_message_reply_markup.await_args_list)
    assert user.last_message is sent[-1] and not user.keep_last_message
    assert user.status == UserState.AUTHENTICATED
    assert user.groups == [group]


@pytest.mark.parametrize("default_model", ["server-embedding", None])
def test_group_model_settings_show_the_actual_server_default(load_module, run_async, monkeypatch, default_model):
    module = load_module("settings/groups.py")
    if default_model:
        monkeypatch.setenv("EMBEDDING_MODEL", default_model)
    else:
        monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    group = UserGroupDBObject(group_id=1, name="Friends", password_hash="hash", created_by=123)
    module.get_app_user_groups.return_value = [group]
    monkeypatch.setattr(module, "user_info", {123: AppUser(user_id=123, status=UserState.AUTHENTICATED)})

    run_async(module.show_group(123, 1))
    assert f"{default_model or 'Not configured'} (server default)" in module.send_message.await_args.args[1]
    run_async(module.request_group_model_change(123, 1))
    assert f"Server default: {default_model or 'Not configured'}" in module.send_message.await_args.args[1]
    assert module.send_message.await_args.kwargs["keep_in_history"] is True


def test_group_prompts_work_without_signup_state(load_module, run_async, monkeypatch):
    module = load_module('database/groups.py')
    user = AppUser(user_id=123, status=UserState.AUTHENTICATED)
    monkeypatch.setitem(module.user_info, 123, user)

    run_async(module.create_group_request(123))
    assert user.status == UserState.WAIT_FOR_NEW_GROUP_NAME
    run_async(module.query_password_confirmation(123, 'password'))
    assert user.status == UserState.WAIT_FOR_NEW_GROUP_PASSWORD_CONFIRMATION
    assert user.pending_group_password_hash == module.hash_password('password')
    run_async(module.join_group_request(123))
    assert user.status == UserState.WAIT_FOR_GROUP_NAME


def test_join_updates_groups_and_connected_host_without_duplicates(load_module, run_async, monkeypatch):
    module = load_module('database/groups.py')
    group = UserGroupDBObject(group_id=1, name='Friends',
                              password_hash=module.hash_password('password'), created_by=123)
    user = AppUser(user_id=123, status=UserState.WAIT_FOR_GROUP_PASSWORD,
                   groups=[], pending_group=group)
    monkeypatch.setitem(module.user_info, 123, user)
    server = sys.modules['server']
    host = SimpleNamespace(user=user)
    server.available_hosts[123] = host
    server.available_group_hosts = {}

    run_async(module.check_group_password(123, 'password'))
    user.pending_group = group
    run_async(module.check_group_password(123, 'password'))

    assert user.groups == [group]
    assert server.available_group_hosts[1] == [host]
    assert user.pending_group is None
    assert user.status == UserState.AUTHENTICATED


def test_create_preserves_existing_groups_and_dialogs(load_module, run_async, monkeypatch):
    module = load_module('database/groups.py')
    old_group = UserGroupDBObject(group_id=1, name='Old', password_hash='hash', created_by=123)
    new_group = UserGroupDBObject(group_id=2, name='New', password_hash='hash', created_by=123)
    user = AppUser(user_id=123, status=UserState.WAIT_FOR_NEW_GROUP_PASSWORD_CONFIRMATION,
                   groups=[old_group], dialogs=[], pending_group_name='New',
                   pending_group_password_hash=module.hash_password('password'))
    monkeypatch.setitem(module.user_info, 123, user)
    cursor = MagicMock()
    cursor.fetchone.return_value = new_group
    module.connection.cursor.return_value = MagicMock()
    module.connection.cursor.return_value.__enter__.return_value = cursor
    sys.modules['server'].available_group_hosts = {}

    run_async(module.process_password_confirmation(123, 'password'))
    run_async(module.save_group_embedding_model(123, 'provider/model-v2'))

    assert user.groups == [old_group, new_group]
    assert user.dialogs == []
    assert user.pending_group_name is None
    assert user.status == UserState.AUTHENTICATED
    assert cursor.execute.call_args_list[0].args[1][-1] == 'provider/model-v2'


def test_creation_saves_membership_before_notification(load_module, run_async, monkeypatch):
    module = load_module('database/groups.py')
    group = UserGroupDBObject(group_id=1, name='Friends', password_hash='hash', created_by=123)
    user = AppUser(user_id=123, status=UserState.WAIT_FOR_NEW_GROUP_PASSWORD_CONFIRMATION,
                   groups=[], pending_group_name='Friends',
                   pending_group_password_hash=module.hash_password('password'))
    monkeypatch.setattr(module, 'user_info', {123: user})
    cursor = MagicMock()
    cursor.fetchone.return_value = group
    module.connection.cursor.return_value = MagicMock()
    module.connection.cursor.return_value.__enter__.return_value = cursor
    order = []
    cursor.execute.side_effect = lambda *args: order.append('insert')
    module.connection.commit.side_effect = lambda: order.append('commit')

    async def notify(*args, **kwargs):
        assert order == ['insert', 'insert', 'commit']
        raise Forbidden('Bot blocked')

    module.send_message.side_effect = notify
    run_async(module.create_group(123, None))

    assert user.groups == [group]
    assert user.status == UserState.AUTHENTICATED
    module.connection.rollback.assert_not_called()


def test_membership_failure_rolls_back_group_creation(load_module, run_async, monkeypatch):
    module = load_module('database/groups.py')
    group = UserGroupDBObject(group_id=1, name='Friends', password_hash='hash', created_by=123)
    user = AppUser(user_id=123, status=UserState.WAIT_FOR_NEW_GROUP_PASSWORD_CONFIRMATION,
                   groups=[], pending_group_name='Friends',
                   pending_group_password_hash=module.hash_password('password'))
    monkeypatch.setattr(module, 'user_info', {123: user})
    cursor = MagicMock()
    cursor.fetchone.return_value = group
    cursor.execute.side_effect = [None, RuntimeError('Cannot insert membership')]
    module.connection.cursor.return_value = MagicMock()
    module.connection.cursor.return_value.__enter__.return_value = cursor

    with pytest.raises(RuntimeError):
        run_async(module.create_group(123, None))

    module.connection.rollback.assert_called_once()
    module.connection.commit.assert_not_called()
    module.send_message.assert_not_awaited()
    assert user.groups == []


def test_model_edit_requires_creator_and_updates_members_cache(load_module, run_async, monkeypatch):
    module = load_module("database/groups.py")
    group = UserGroupDBObject(group_id=1, name="Friends", password_hash="hash", created_by=123)
    member_group = group.model_copy()
    owner = AppUser(user_id=123, status=UserState.WAIT_FOR_GROUP_EMBEDDING_MODEL,
                    groups=[group], pending_group=group)
    member = AppUser(user_id=456, status=UserState.WAIT_FOR_GROUP_EMBEDDING_MODEL,
                     groups=[member_group], pending_group=member_group)
    monkeypatch.setattr(module, "user_info", {123: owner, 456: member})
    settings = ModuleType("settings.groups")
    settings.group_settings = AsyncMock()
    monkeypatch.setitem(sys.modules, "settings.groups", settings)

    run_async(module.save_group_embedding_model(456, "other-model"))
    module.connection.cursor.assert_not_called()

    cursor = MagicMock()
    cursor.fetchone.return_value = (1,)
    module.connection.cursor.return_value = MagicMock()
    module.connection.cursor.return_value.__enter__.return_value = cursor
    run_async(module.save_group_embedding_model(123, "new-model"))

    query, parameters = cursor.execute.call_args.args
    assert "created_by = %s" in query
    assert parameters == ("new-model", 1, 123)
    assert owner.groups[0].embedding_model == member.groups[0].embedding_model == "new-model"


@pytest.mark.parametrize("user_id", [123, 456])
def test_only_creator_sees_model_edit_in_groups_menu(load_module, run_async, monkeypatch, user_id):
    module = load_module("settings/groups.py")
    group = UserGroupDBObject(group_id=1, name="Friends", password_hash="hash", created_by=123,
                              embedding_model="group-model")
    module.get_app_user_groups.return_value = [group]
    monkeypatch.setitem(module.user_info, user_id, AppUser(user_id=user_id, status=UserState.AUTHENTICATED))

    run_async(module.group_settings(user_id))
    keyboard = module.send_message.call_args.kwargs["reply_markup"].inline_keyboard
    assert keyboard[0][0].callback_data == "group:1"
    run_async(module.show_group(user_id, 1))

    assert "group-model" in module.send_message.call_args.args[1]
    keyboard = module.send_message.call_args.kwargs["reply_markup"].inline_keyboard
    assert any(button.callback_data == "group_model:1" for row in keyboard for button in row) == (user_id == 123)
    assert any(button.callback_data == "group_delete:1" for row in keyboard for button in row) == (user_id == 123)


def test_group_deletion_removes_host_heap_and_cached_memberships(load_module, monkeypatch):
    module = load_module("database/groups.py")
    owned = UserGroupDBObject(group_id=1, name="Friends", password_hash="hash", created_by=123)
    other = UserGroupDBObject(group_id=2, name="Work", password_hash="hash", created_by=456)
    owner = AppUser(user_id=123, status=UserState.AUTHENTICATED, groups=[owned])
    member = AppUser(user_id=456, status=UserState.AUTHENTICATED, groups=[owned, other], pending_group=owned)
    monkeypatch.setattr(module, "user_info", {123: owner, 456: member})
    cursor = MagicMock()
    cursor.fetchone.side_effect = [None, (1,)]
    module.connection.cursor.return_value = MagicMock()
    module.connection.cursor.return_value.__enter__.return_value = cursor
    server = sys.modules["server"]
    server.available_group_hosts.update({1: [object()], 2: [object()]})

    assert module.delete_group(456, 1) is False
    assert 1 in server.available_group_hosts and owner.groups == [owned]
    assert module.delete_group(123, 1) is True

    query, params = cursor.execute.call_args.args
    assert "AND created_by = %s" in query and params == (1, 123)
    assert owner.groups == [] and member.groups == [other]
    assert member.pending_group is None
    assert set(server.available_group_hosts) == {2}


def test_group_deletion_requires_matching_confirmation(load_module, run_async, monkeypatch):
    module = load_module("settings/groups.py")
    group = UserGroupDBObject(group_id=1, name="Friends", password_hash="hash", created_by=123)
    owner = AppUser(user_id=123, status=UserState.WAIT_FOR_GROUP_SETTINGS)
    member = AppUser(user_id=456, status=UserState.WAIT_FOR_GROUP_SETTINGS)
    monkeypatch.setattr(module, "user_info", {123: owner, 456: member})
    module.get_app_user_groups.return_value = [group]

    run_async(module.request_group_delete(456, 1))
    module.send_message.assert_not_awaited()
    run_async(module.confirm_group_delete(123, 1))
    run_async(module.request_group_delete(123, 1))
    run_async(module.confirm_group_delete(123, 2))
    module.delete_group.assert_not_called()

    module.delete_group.return_value = True
    module.get_app_user_groups.return_value = []
    run_async(module.confirm_group_delete(123, 1))

    module.delete_group.assert_called_once_with(123, 1)
    assert owner.status == UserState.WAIT_FOR_GROUP_SETTINGS
    assert "Group deleted." in module.send_message.call_args.args[1]
