import sys
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.error import BadRequest

from utils.classes import AppUser, UserGroupDBObject, UserState


def telegram_message(message_id):
    return Message(message_id=message_id, chat=Chat(123, "private"), date=datetime.now(timezone.utc))


def load_handlers(load_module, monkeypatch):
    imports = {
        "bot.commands.logout": ["log_out_confirmation", "log_out_request"],
        "utils.auth": ["process_phone_number", "process_code", "process_password", "process_bot_password",
                       "send_one_time_code", "check_bot_password", "process_bot_password_confirmation"],
        "features.search": ["search_request", "process_search_chat", "process_search_text"],
        "features.summarize": ["summarize_request", "process_summarize_query"],
        "settings": ["settings"],
        "settings.groups": ["group_settings", "show_group", "request_group_model_change",
                            "request_group_delete", "confirm_group_delete"],
        "settings.allowed_dialogs": ["display_allowed_dialogs", "change_allowed_dialogs",
                                     "display_new_allowed_dialogs_options", "query_new_allowed_dialogs_categories",
                                     "query_new_allowed_dialogs"],
        "settings.history_size": ["display_history_size", "change_history_size", "query_new_history_size"],
        "database.groups": ["process_password_confirmation", "query_password_confirmation", "query_new_group_password",
                            "join_group", "check_group_password", "create_group_request", "join_group_request",
                            "save_group_embedding_model"],
        "database.users": ["flip_user_state", "update_ordering"],
        "utils.helpers": ["next_page", "prev_page"],
        "utils.check_authentication": ["offer_host_type", "query_phone_number"],
    }
    for name, attributes in imports.items():
        module = sys.modules.get(name, ModuleType(name))
        monkeypatch.setitem(sys.modules, name, module)
        for attribute in attributes:
            monkeypatch.setattr(module, attribute, AsyncMock(), raising=False)
    return load_module("bot/command_handlers.py")


@pytest.mark.parametrize(("state", "action", "destination", "args", "kwargs"), [
    (UserState.WAIT_FOR_SEARCH_CHAT, "back", "menu", (), {"user_id": 123, "edit": True}),
    (UserState.WAIT_FOR_GROUP_SETTINGS, "groups_back", "settings", (), {"user_id": 123, "edit": True}),
    (UserState.WAIT_FOR_NEW_GROUP_PASSWORD, "groups", "group_settings", (123,), {"edit": True}),
    (UserState.WAIT_FOR_GROUP_DELETE_CONFIRMATION, "group:1", "show_group", (123, 1), {}),
])
def test_back_goes_to_expected_screen(load_module, run_async, monkeypatch, state, action, destination, args, kwargs):
    module = load_handlers(load_module, monkeypatch)
    message = telegram_message(10)
    group = UserGroupDBObject(group_id=1, name="Friends", password_hash="hash", created_by=123)
    user = AppUser(user_id=123, status=state, last_message=message, pending_group=group)
    monkeypatch.setattr(module, "user_info", {123: user})
    monkeypatch.setattr(module, "uninitialized_users", {})
    query = SimpleNamespace(data=action, message=message, answer=AsyncMock())

    run_async(module.process_button(SimpleNamespace(effective_user=SimpleNamespace(id=123), callback_query=query), None))

    getattr(module, destination).assert_awaited_once_with(*args, **kwargs)
    module.confirm_group_delete.assert_not_awaited()


def test_notifications_do_not_replace_current_menu(load_module, run_async, monkeypatch):
    module = load_module("utils/helpers.py")
    menu = telegram_message(10)
    notice = telegram_message(11)
    user = AppUser(user_id=123, status=UserState.WAIT_FOR_SETTINGS_CHOICE, last_message=menu)
    monkeypatch.setattr(module, "user_info", {123: user})
    module.application.bot.send_message.return_value = notice

    run_async(module.send_message(123, "Downloading messages..."))

    assert user.last_message is menu


def test_deleted_menu_is_replaced_when_back_tries_to_edit_it(load_module, run_async, monkeypatch):
    module = load_module("utils/helpers.py")
    gone = telegram_message(10)
    replacement = telegram_message(11)
    user = AppUser(user_id=123, status=UserState.WAIT_FOR_SETTINGS_CHOICE, last_message=gone)
    monkeypatch.setattr(module, "user_info", {123: user})
    monkeypatch.setattr(module.application.bot, "edit_message_text",
                        AsyncMock(side_effect=BadRequest("Message to edit not found")), raising=False)
    module.application.bot.send_message.return_value = replacement
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("Groups", callback_data="groups")]])

    assert run_async(module.send_message(123, "Settings", reply_markup=markup, edit=True)) is replacement
    assert user.last_message is replacement
