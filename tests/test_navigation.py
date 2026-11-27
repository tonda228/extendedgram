from types import SimpleNamespace

from telegram import InlineKeyboardButton

from utils.classes import UserState


def test_repeated_deselection_does_not_raise(load_module):
    module = load_module('utils/keyboard.py')
    button = InlineKeyboardButton('Chat: ✅', callback_data='1')
    selected = {'1'}

    first = module.update_inline_button(button, '1', selected)
    second = module.update_inline_button(button, '1', selected)

    assert selected == set()
    assert first.text == second.text == 'Chat: ❌'


def test_logout_before_signup_does_not_show_confirmation(load_module, run_async, monkeypatch):
    module = load_module('bot/commands/logout.py')
    monkeypatch.setattr(module, 'user_info', {})
    module.check_authentication.return_value = False

    run_async(module.log_out_request(user_id=123))

    module.check_authentication.assert_awaited_once_with(123)
    module.send_message.assert_not_awaited()


def test_new_search_discards_previous_operation_selection(load_module, run_async, monkeypatch):
    module = load_module('features/search.py')
    old = (SimpleNamespace(title='Old'), None)
    current = (SimpleNamespace(title='Current'), None)
    user = SimpleNamespace(dialogs=[old], chosen_ids={'7'}, cur_page=5, order_by='date', search_in_progress=False)
    monkeypatch.setattr(module, 'user_info', {123: user})
    module.get_allowed_dialogs.return_value = [current]

    run_async(module.search_request(user_id=123))

    assert user.dialogs == [current]
    assert user.chosen_ids == set()
    assert user.cur_page == 0
    assert user.status == UserState.WAIT_FOR_SEARCH_CHAT
    module.get_allowed_dialogs.assert_awaited_once_with(123)


def test_search_pagination_preserves_selection(load_module, run_async, monkeypatch):
    module = load_module('features/search.py')
    dialogs = [(SimpleNamespace(title=f'Chat {index}'), None) for index in range(module.PAGE_SIZE + 1)]
    user = SimpleNamespace(dialogs=dialogs, chosen_ids={'0'}, cur_page=1, order_by='date', search_in_progress=False)
    monkeypatch.setattr(module, 'user_info', {123: user})

    run_async(module.search_request(user_id=123, edit=True, new_request=False))

    assert user.dialogs is dialogs
    assert user.chosen_ids == {'0'}
    assert user.cur_page == 1
    module.get_allowed_dialogs.assert_not_awaited()


def test_new_summary_refreshes_dialogs_and_excludes_read_chats(load_module, run_async, monkeypatch):
    module = load_module('features/summarize.py')
    read = (SimpleNamespace(title='Read', unread_count=0), None)
    unread = (SimpleNamespace(title='Unread', unread_count=5), None)
    user = SimpleNamespace(dialogs=[read], chosen_ids={'9'}, cur_page=4)
    monkeypatch.setattr(module, 'user_info', {123: user})
    module.get_allowed_dialogs.return_value = [read, unread]
    module.get_unread_count.side_effect = lambda dialog: dialog[0].unread_count

    run_async(module.summarize_request(user_id=123))

    assert user.dialogs == [unread]
    assert user.chosen_ids is None
    assert user.cur_page == 0
    module.get_allowed_dialogs.assert_awaited_once_with(123)


def test_summary_pagination_keeps_current_page(load_module, run_async, monkeypatch):
    module = load_module('features/summarize.py')
    dialogs = [(SimpleNamespace(title=f'Chat {index}', unread_count=1), None)
               for index in range(module.PAGE_SIZE + 1)]
    user = SimpleNamespace(dialogs=dialogs, cur_page=1)
    monkeypatch.setattr(module, 'user_info', {123: user})
    module.get_unread_count.return_value = 1

    run_async(module.summarize_request(user_id=123, edit=True, new_request=False))

    assert user.dialogs is dialogs
    assert user.cur_page == 1
    module.get_allowed_dialogs.assert_not_awaited()


def test_permission_settings_start_without_search_selection(load_module, run_async, monkeypatch):
    module = load_module('settings/allowed_dialogs.py')
    user = SimpleNamespace(status=UserState.WAIT_FOR_CHANGE_ALLOWED_DIALOGS_CONFIRMATION,
                           dialogs=['old'], chosen_ids={'3'}, allowed_dialogs_options={'users'},
                           cur_page=2, last_category='users')
    monkeypatch.setattr(module, 'user_info', {123: user})

    run_async(module.display_new_allowed_dialogs_options(123, None))

    assert user.dialogs is None
    assert user.chosen_ids is None
    assert user.allowed_dialogs_options == set()
    assert user.cur_page == 0


def test_return_to_permission_options_keeps_current_choices(load_module, run_async, monkeypatch):
    module = load_module('settings/allowed_dialogs.py')
    user = SimpleNamespace(status=UserState.WAIT_FOR_ALLOWED_DIALOGS_CATEGORY_CHOICE,
                           dialogs=['current'], chosen_ids={'3'}, allowed_dialogs_options={'choose'},
                           cur_page=1, last_category='users')
    monkeypatch.setattr(module, 'user_info', {123: user})

    run_async(module.display_new_allowed_dialogs_options(123, None))

    assert user.chosen_ids == {'3'}
    assert user.allowed_dialogs_options == {'choose'}


def test_search_requires_a_selected_chat(load_module, run_async, monkeypatch):
    module = load_module('features/search.py')
    user = SimpleNamespace(dialogs=[(SimpleNamespace(title='Chat'), None)], chosen_ids=set())
    monkeypatch.setattr(module, 'user_info', {123: user})

    run_async(module.process_search_chat(123))

    module.store_dialog.assert_not_called()
    assert 'at least one' in module.send_message.call_args.args[1]
    module.disable_inline_keyboard.assert_not_awaited()


def test_confirming_chats_retires_selection_before_next_prompt(load_module, run_async, monkeypatch):
    module = load_module("features/search.py")
    dialog = (SimpleNamespace(id=10, title="Chat"), None)
    user = SimpleNamespace(dialogs=[dialog], chosen_ids={"0"})
    monkeypatch.setattr(module, "user_info", {123: user})

    async def retire(user_id):
        assert user.status == UserState.WAIT_FOR_REQUEST_ANSWER
        module.send_message.assert_not_awaited()

    module.disable_inline_keyboard.side_effect = retire
    user.history_size = 100
    user.pending_search_query = None
    user.search_since = None
    user.search_until = None

    run_async(module.process_search_chat(123))

    module.disable_inline_keyboard.assert_awaited_once_with(123)
    assert user.status == UserState.WAIT_FOR_SEARCH_TEXT
