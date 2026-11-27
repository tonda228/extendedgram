from types import SimpleNamespace

from utils.classes import AppUser, UserState


def test_menu_shows_available_actions(load_module, run_async, monkeypatch):
    module = load_module("bot/commands/menu.py")
    user = AppUser(user_id=123, status=UserState.WAIT_FOR_SEARCH_TEXT)
    monkeypatch.setitem(module.user_info, 123, user)

    run_async(module.menu(user_id=123, edit=True))

    module.reset_idle_timer.assert_called_once_with(123)
    args, kwargs = module.send_message.call_args
    assert args[0] == 123
    assert kwargs["edit"] is True
    buttons = kwargs["reply_markup"].inline_keyboard[0]
    assert [button.callback_data for button in buttons] == ["summarize", "search", "settings"]
    assert user.status == UserState.AUTHENTICATED


def test_processing_error_is_shown_in_the_menu_message(load_module, run_async, monkeypatch):
    module = load_module("bot/commands/menu.py")
    monkeypatch.setitem(module.user_info, 123, AppUser(user_id=123, status=UserState.AUTHENTICATED))

    run_async(module.menu(user_id=123, notice="Could not process the messages."))

    module.send_message.assert_awaited_once()
    assert module.send_message.await_args.args[1] == "Could not process the messages.\n\nWhere do you wish to continue?"
    assert module.send_message.await_args.kwargs["reply_markup"] is not None


def test_menu_stops_when_user_is_not_authenticated(load_module, run_async):
    module = load_module("bot/commands/menu.py")
    module.check_authentication.return_value = False

    run_async(module.menu(user_id=123))

    module.send_message.assert_not_awaited()
    module.reset_idle_timer.assert_not_called()


def test_menu_reads_user_id_from_update(load_module, run_async, monkeypatch):
    module = load_module("bot/commands/menu.py")
    monkeypatch.setitem(module.user_info, 123, AppUser(user_id=123, status=UserState.AUTHENTICATED))
    update = SimpleNamespace(effective_user=SimpleNamespace(id=123))

    run_async(module.menu(update=update))

    module.check_authentication.assert_awaited_once_with(123)


def test_logout_request_asks_for_confirmation(load_module, run_async, monkeypatch):
    module = load_module("bot/commands/logout.py")
    user = AppUser(user_id=123, status=UserState.AUTHENTICATED)
    monkeypatch.setitem(module.user_info, 123, user)

    run_async(module.log_out_request(user_id=123))

    assert user.status == UserState.WAIT_FOR_LOG_OUT_CONFIRMATION
    markup = module.send_message.call_args.args[2]
    assert [button.callback_data for button in markup.inline_keyboard[0]] == ["Yes", "No"]


def test_start_authenticates_using_user_id(load_module, run_async, monkeypatch):
    module = load_module("bot/commands/start.py")
    user = AppUser(user_id=123, status=UserState.LOGGED_OUT)
    monkeypatch.setitem(module.user_info, 123, user)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123), effective_chat=SimpleNamespace(id=456),
    )

    run_async(module.start(update, None))

    module.send_message.assert_awaited_once()
    assert module.send_message.call_args.kwargs["chat_id"] == 456
    module.check_authentication.assert_awaited_once_with(123)
    assert user.status == UserState.AUTHENTICATED
    module.menu.assert_awaited_once_with(update, None)


def test_start_does_not_open_menu_before_authentication(load_module, run_async):
    module = load_module("bot/commands/start.py")
    module.check_authentication.return_value = False
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123), effective_chat=SimpleNamespace(id=456),
    )

    run_async(module.start(update, None))

    module.menu.assert_not_awaited()
