import sys
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
        "features.search": ["search_request", "process_search_chat", "process_search_text", "submit_search_query", "start_pending_search", "query_search_dates", "process_search_dates", "query_search_text"],
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


def test_expired_text_input_is_not_used_as_search_query_or_password(load_module, run_async, monkeypatch):
    module = load_handlers(load_module, monkeypatch)
    session = load_module("utils/session.py")
    checks = load_module("utils/check_authentication.py")
    user = AppUser(user_id=123, status=UserState.WAIT_FOR_SEARCH_TEXT, last_activity=0)
    for target in (module, session, checks):
        monkeypatch.setattr(target, "user_info", {123: user})
    monkeypatch.setattr(module, "uninitialized_users", {})
    monkeypatch.setattr(checks, "uninitialized_users", {})
    monkeypatch.setattr(session, "monotonic", lambda: 1800)
    checks.expire_idle_session = session.expire_idle_session
    checks.record_activity = session.record_activity
    module.check_authentication = checks.check_authentication
    update = SimpleNamespace(effective_user=SimpleNamespace(id=123), message=SimpleNamespace(text="A new question"))

    run_async(module.process_message(update, None))

    assert user.status == UserState.LOGGED_OUT
    module.submit_search_query.assert_not_awaited()
    module.check_bot_password.assert_not_awaited()
    session.send_message.assert_awaited_once()


def test_logged_out_text_is_routed_only_to_password_check(load_module, run_async, monkeypatch):
    module = load_handlers(load_module, monkeypatch)
    user = AppUser(user_id=123, status=UserState.LOGGED_OUT)
    monkeypatch.setattr(module, "user_info", {123: user})
    monkeypatch.setattr(module, "uninitialized_users", {})
    update = SimpleNamespace(effective_user=SimpleNamespace(id=123), message=SimpleNamespace(text="password"))

    run_async(module.process_message(update, None))

    module.check_bot_password.assert_awaited_once_with(123, "password")
    module.check_authentication.assert_not_awaited()


@pytest.mark.parametrize("state,render,dialogs", [
    (UserState.WAIT_FOR_SEARCH_CHAT, "search_request", True),
    (UserState.WAIT_FOR_SUMMARIZE_CHAT, "summarize_request", True),
    (UserState.WAIT_FOR_CHANGE_ALLOWED_DIALOGS_CONFIRMATION, "display_allowed_dialogs", True),
    (UserState.WAIT_FOR_ALLOWED_DIALOGS_MANUAL_CHOICE, "query_new_allowed_dialogs", False),
])
def test_pager_clicks_are_routed_to_edit_existing_message(load_module, run_async, monkeypatch, state, render, dialogs):
    module = load_handlers(load_module, monkeypatch)
    helpers = load_module("utils/helpers.py")
    module.next_page = helpers.next_page
    message = telegram_message(10)
    user = AppUser(user_id=123, status=state, last_message=message)
    monkeypatch.setattr(module, "user_info", {123: user})
    monkeypatch.setattr(module, "uninitialized_users", {})
    if dialogs:
        user.dialogs = [None] * 7
    user.category_dialogs_count = 7
    query = SimpleNamespace(data="next", message=message, answer=AsyncMock())

    run_async(module.process_button(SimpleNamespace(effective_user=SimpleNamespace(id=123), callback_query=query), None))

    assert user.cur_page == 1
    if render in ("search_request", "summarize_request"):
        getattr(module, render).assert_awaited_once_with(user_id=123, edit=True, new_request=False)
    elif render == "display_allowed_dialogs":
        module.display_allowed_dialogs.assert_awaited_once_with(123, edit=True, new_request=False)
    else:
        module.query_new_allowed_dialogs.assert_awaited_once_with(123, query)
    assert query.message is message


@pytest.mark.parametrize(("state", "action", "destination", "args", "kwargs"), [
    (UserState.WAIT_FOR_SEARCH_CHAT, "back", "menu", (), {"user_id": 123, "edit": True}),
    (UserState.WAIT_FOR_GROUP_SETTINGS, "groups_back", "settings", (), {"user_id": 123, "edit": True}),
    (UserState.WAIT_FOR_NEW_GROUP_PASSWORD, "groups", "group_settings", (123,), {"edit": True}),
    (UserState.WAIT_FOR_GROUP_DELETE_CONFIRMATION, "group:1", "show_group", (123, 1), {}),
    (UserState.WAIT_FOR_SEARCH_DATES, "back", "query_search_text", (123,), {"edit": True}),
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


@pytest.mark.parametrize("state", [UserState.WAIT_FOR_SEARCH_PERIOD, UserState.WAIT_FOR_SEARCH_DATES])
@pytest.mark.parametrize("enabled", [False, True])
def test_ignore_images_button_updates_setting_without_starting_search(load_module, run_async, monkeypatch, state, enabled):
    module = load_handlers(load_module, monkeypatch)
    search = load_module("features/search.py")
    module.query_search_text = search.query_search_text
    module.query_search_dates = search.query_search_dates
    user = AppUser(user_id=123, status=state, last_message=telegram_message(10),
                   process_images=enabled, pending_search_query="When?")
    for target in (module, search):
        monkeypatch.setattr(target, "user_info", {123: user})
    monkeypatch.setattr(module, "uninitialized_users", {})
    module.flip_user_state = Mock(side_effect=lambda user_id, column: setattr(user, column, not getattr(user, column)))
    query = SimpleNamespace(data="search_ignore_images", message=user.last_message, answer=AsyncMock())

    run_async(module.process_button(SimpleNamespace(effective_user=SimpleNamespace(id=123), callback_query=query), None))

    module.flip_user_state.assert_called_once_with(123, "process_images")
    assert user.process_images is not enabled
    assert user.status == state
    assert user.pending_search_query == "When?"
    module.start_pending_search.assert_not_awaited()
    buttons = search.send_message.await_args.kwargs["reply_markup"].inline_keyboard
    button = next(button for row in buttons for button in row if button.callback_data == "search_ignore_images")
    assert button.text == f"Ignore unprocessed images: {'✅' if enabled else '❌'}"
    assert search.send_message.await_args.kwargs["edit"] is True


def test_notifications_do_not_replace_current_menu(load_module, run_async, monkeypatch):
    module = load_module("utils/helpers.py")
    menu = telegram_message(10)
    notice = telegram_message(11)
    user = AppUser(user_id=123, status=UserState.WAIT_FOR_SETTINGS_CHOICE, last_message=menu)
    monkeypatch.setattr(module, "user_info", {123: user})
    module.application.bot.send_message.return_value = notice

    run_async(module.send_message(123, "Downloading messages..."))

    assert user.last_message is menu


def test_new_screen_removes_old_screen_and_notice_but_keeps_answer(load_module, run_async, monkeypatch):
    module = load_module("utils/helpers.py")
    old_screen, notice, answer, new_screen = [telegram_message(index) for index in (10, 11, 12, 13)]
    user = AppUser(user_id=123, status=UserState.AUTHENTICATED, last_message=old_screen, temporary_message=notice)
    monkeypatch.setattr(module, "user_info", {123: user})
    module.application.bot.send_message.side_effect = [answer, new_screen]
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("Search", callback_data="search")]])

    async def flow():
        await module.send_message(123, "The model's answer")
        module.application.bot.delete_message.assert_not_awaited()
        await module.send_message(123, "Menu", reply_markup=markup)

    run_async(flow())

    deleted = [call.kwargs["message_id"] for call in module.application.bot.delete_message.await_args_list]
    assert deleted == [10, 11]
    assert user.last_message is new_screen
    assert user.temporary_message is None


def test_temporary_notices_replace_only_the_previous_notice(load_module, run_async, monkeypatch):
    module = load_module("utils/helpers.py")
    screen, previous, current = [telegram_message(index) for index in (10, 11, 12)]
    user = AppUser(user_id=123, status=UserState.AUTHENTICATED, last_message=screen, temporary_message=previous)
    monkeypatch.setattr(module, "user_info", {123: user})
    module.application.bot.send_message.return_value = current

    run_async(module.send_message(123, "Host disconnected", temporary=True))

    module.application.bot.delete_message.assert_awaited_once_with(chat_id=123, message_id=11)
    assert user.last_message is screen
    assert user.temporary_message is current


def test_editing_menu_keeps_its_message_and_removes_temporary_notice(load_module, run_async, monkeypatch):
    module = load_module("utils/helpers.py")
    screen, notice = [telegram_message(index) for index in (10, 11)]
    user = AppUser(user_id=123, status=UserState.AUTHENTICATED, last_message=screen, temporary_message=notice)
    monkeypatch.setattr(module, "user_info", {123: user})
    monkeypatch.setattr(module.application.bot, "edit_message_text", AsyncMock(return_value=screen), raising=False)
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("Next", callback_data="next")]])

    run_async(module.send_message(123, "Page 2", reply_markup=markup, edit=True))

    module.application.bot.send_message.assert_not_awaited()
    module.application.bot.delete_message.assert_awaited_once_with(chat_id=123, message_id=11)
    assert user.last_message is screen


def test_failed_new_screen_does_not_remove_current_screen(load_module, run_async, monkeypatch):
    module = load_module("utils/helpers.py")
    screen = telegram_message(10)
    user = AppUser(user_id=123, status=UserState.AUTHENTICATED, last_message=screen)
    monkeypatch.setattr(module, "user_info", {123: user})
    module.application.bot.send_message.side_effect = BadRequest("Invalid message")
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("Search", callback_data="search")]])

    assert run_async(module.send_message(123, "Menu", reply_markup=markup)) is None

    module.application.bot.delete_message.assert_not_awaited()
    assert user.last_message is screen


def test_undeletable_old_screen_has_buttons_removed(load_module, run_async, monkeypatch):
    module = load_module("utils/helpers.py")
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("Search", callback_data="search")]])
    old_screen = Message(message_id=10, chat=Chat(123, "private"), date=datetime.now(timezone.utc), reply_markup=markup)
    new_screen = telegram_message(11)
    user = AppUser(user_id=123, status=UserState.AUTHENTICATED, last_message=old_screen)
    monkeypatch.setattr(module, "user_info", {123: user})
    module.application.bot.send_message.return_value = new_screen
    module.application.bot.delete_message.side_effect = BadRequest("Message cannot be deleted")

    assert run_async(module.send_message(123, "Menu", reply_markup=markup)) is new_screen

    module.application.bot.edit_message_reply_markup.assert_awaited_once_with(chat_id=123, message_id=10, reply_markup=None)
    assert user.last_message is new_screen


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


def test_back_keeps_answered_group_prompt_instead_of_editing_it(load_module, run_async, monkeypatch):
    module = load_module("utils/helpers.py")
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("Back to groups", callback_data="groups")]])
    prompt = Message(message_id=10, chat=Chat(123, "private"), date=datetime.now(timezone.utc),
                     text="Enter your group name.", reply_markup=markup)
    user = AppUser(user_id=123, status=UserState.WAIT_FOR_NEW_GROUP_NAME,
                   last_message=prompt, keep_last_message=True)
    monkeypatch.setattr(module, "user_info", {123: user})
    replacement = telegram_message(11)
    module.application.bot.send_message.return_value = replacement
    edit = AsyncMock()
    monkeypatch.setattr(module.application.bot, "edit_message_text", edit, raising=False)

    run_async(module.send_message(123, "Groups", reply_markup=markup, edit=True))

    edit.assert_not_awaited()
    module.application.bot.delete_message.assert_not_awaited()
    module.application.bot.edit_message_reply_markup.assert_awaited_once_with(chat_id=123, message_id=10, reply_markup=None)
    assert prompt.text == "Enter your group name."
    assert user.last_message is replacement and not user.keep_last_message


def test_failed_button_cleanup_keeps_group_prompt_and_invalidates_it(load_module, run_async, monkeypatch):
    module = load_module("utils/keyboard.py")
    user = AppUser(user_id=123, status=UserState.WAIT_FOR_NEW_GROUP_NAME,
                   last_message=telegram_message(10), keep_last_message=True)
    monkeypatch.setattr(module, "user_info", {123: user})
    module.application.bot.edit_message_reply_markup.side_effect = BadRequest("Message can't be edited")

    run_async(module.disable_inline_keyboard(123))

    module.application.bot.delete_message.assert_not_awaited()
    assert user.last_message is None and not user.keep_last_message


@pytest.mark.parametrize("action", ["next", "0", "confirm", "settings", "search", "summarize"])
def test_stale_message_buttons_cannot_change_current_screen(load_module, run_async, monkeypatch, action):
    module = load_handlers(load_module, monkeypatch)
    user = AppUser(user_id=123, status=UserState.WAIT_FOR_SEARCH_TEXT, last_message=telegram_message(20))
    monkeypatch.setattr(module, "user_info", {123: user})
    monkeypatch.setattr(module, "uninitialized_users", {})
    query = SimpleNamespace(data=action, message=telegram_message(10), answer=AsyncMock())

    run_async(module.process_button(SimpleNamespace(effective_user=SimpleNamespace(id=123), callback_query=query), None))

    assert user.status == UserState.WAIT_FOR_SEARCH_TEXT
    module.settings.assert_not_awaited()
    module.search_request.assert_not_awaited()
    module.summarize_request.assert_not_awaited()
    module.process_search_chat.assert_not_awaited()
    module.send_updated_inline_keyboard.assert_not_awaited()


@pytest.mark.parametrize("state", [UserState.WAIT_FOR_SEARCH_PERIOD, UserState.WAIT_FOR_SEARCH_DATES])
def test_history_size_button_starts_saved_query(load_module, run_async, monkeypatch, state):
    module = load_handlers(load_module, monkeypatch)
    search = load_module("features/search.py")
    module.start_pending_search = search.start_pending_search
    search.process_search_text = AsyncMock()
    message = telegram_message(10)
    user = AppUser(user_id=123, status=state, last_message=message, history_size=14, pending_search_query="When?",
                   search_since=datetime(2026, 11, 1, tzinfo=timezone.utc),
                   search_until=datetime(2026, 11, 26, tzinfo=timezone.utc))
    for target in (module, search):
        monkeypatch.setattr(target, "user_info", {123: user})
    monkeypatch.setattr(module, "uninitialized_users", {})
    query = SimpleNamespace(data="search_default_dates", message=message, answer=AsyncMock())

    run_async(module.process_button(SimpleNamespace(effective_user=SimpleNamespace(id=123), callback_query=query), None))

    assert user.search_since is None and user.search_until is None
    assert user.status == UserState.WAIT_FOR_REQUEST_ANSWER
    assert user.pending_search_query is None
    search.process_search_text.assert_awaited_once_with(123, "When?")


@pytest.mark.parametrize("custom_dates", [False, True])
def test_question_then_period_starts_search_without_second_question(load_module, run_async, monkeypatch, custom_dates):
    module = load_handlers(load_module, monkeypatch)
    search = load_module("features/search.py")
    for name in ("submit_search_query", "start_pending_search", "query_search_dates", "process_search_dates", "query_search_text"):
        setattr(module, name, getattr(search, name))
    search.process_search_text = AsyncMock()
    user = AppUser(user_id=123, status=UserState.WAIT_FOR_SEARCH_TEXT, last_message=telegram_message(10))
    for target in (module, search):
        monkeypatch.setattr(target, "user_info", {123: user})
    monkeypatch.setattr(module, "uninitialized_users", {})

    async def flow():
        update = SimpleNamespace(effective_user=SimpleNamespace(id=123), message=SimpleNamespace(text="When is the meeting?"))
        await module.process_message(update, None)
        assert user.status == UserState.WAIT_FOR_SEARCH_PERIOD
        search.process_search_text.assert_not_awaited()
        query = SimpleNamespace(message=user.last_message, answer=AsyncMock(),
                                data="search_dates" if custom_dates else "search_default_dates")
        await module.process_button(SimpleNamespace(effective_user=update.effective_user, callback_query=query), None)
        if custom_dates:
            assert user.status == UserState.WAIT_FOR_SEARCH_DATES
            update.message.text = "01.11.2026 - 25.11.2026"
            await module.process_message(update, None)
        assert user.pending_search_query is None
        search.process_search_text.assert_awaited_once_with(123, "When is the meeting?")
        # A repeated click after starting must not submit the saved query twice.
        await module.process_button(SimpleNamespace(effective_user=update.effective_user, callback_query=query), None)
        search.process_search_text.assert_awaited_once()

    run_async(flow())


@pytest.mark.parametrize("failed", [False, True])
def test_removing_keyboard_invalidates_message_before_api_call(load_module, run_async, monkeypatch, failed):
    module = load_module("utils/keyboard.py")
    user = AppUser(user_id=123, status=UserState.WAIT_FOR_SEARCH_CHAT, last_message=telegram_message(10))
    monkeypatch.setattr(module, "user_info", {123: user})

    async def remove(**kwargs):
        assert user.last_message is None
        if failed:
            raise BadRequest("Message to edit not found")

    delete = AsyncMock(side_effect=remove)
    monkeypatch.setattr(module.application.bot, "delete_message", delete, raising=False)
    edit = AsyncMock()
    monkeypatch.setattr(module.application.bot, "edit_message_reply_markup", edit, raising=False)

    run_async(module.disable_inline_keyboard(123))

    assert user.last_message is None
    delete.assert_awaited_once_with(chat_id=123, message_id=10)
    if failed:
        edit.assert_awaited_once_with(chat_id=123, message_id=10, reply_markup=None)
    else:
        edit.assert_not_awaited()
