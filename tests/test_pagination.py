from types import SimpleNamespace
import pytest
from telethon.tl.types import User


def dialog(index):
    return (SimpleNamespace(id=index, title=f"Chat <{index}>", entity=User(id=index)), None)


@pytest.mark.parametrize("allow_all", [False, True])
def test_allowed_list_pages_edit_the_same_message(load_module, run_async, monkeypatch, allow_all):
    module = load_module("settings/allowed_dialogs.py")
    dialogs = [dialog(index) for index in range(module.PAGE_SIZE + 1)]
    user = SimpleNamespace(allow_all=allow_all, order_by="date", dialogs=None, cur_page=9)
    monkeypatch.setattr(module, "user_info", {123: user})
    module.get_allowed_dialogs.return_value = dialogs
    module.get_full_chat_name.side_effect = lambda item: item[0].title

    run_async(module.display_allowed_dialogs(123))

    text = module.send_message.await_args.args[1]
    assert "Page 1/2" in text and "Chat &lt;0&gt;" in text
    assert f"Chat &lt;{module.PAGE_SIZE}&gt;" not in text
    user.cur_page = 1
    run_async(module.display_allowed_dialogs(123, edit=True, new_request=False))

    text = module.send_message.await_args.args[1]
    assert "Page 2/2" in text and f"Chat &lt;{module.PAGE_SIZE}&gt;" in text
    assert "Chat &lt;0&gt;" not in text
    assert module.send_message.await_args.kwargs["edit"] is True
    module.get_allowed_dialogs.assert_awaited_once()


def test_manual_selection_pagers_edit_and_preserve_choices(load_module, run_async, monkeypatch):
    module = load_module("settings/allowed_dialogs.py")
    dialogs = [dialog(index) for index in range(module.PAGE_SIZE + 1)]
    user = SimpleNamespace(dialogs=dialogs, chosen_ids={"0"}, cur_page=8, last_category="channels")
    monkeypatch.setattr(module, "user_info", {123: user})

    # Entering a different category resets the previous category's page.
    run_async(module.query_new_allowed_dialogs(123, SimpleNamespace(data="users")))
    assert user.cur_page == 0
    assert user.category_dialogs_count == len(dialogs)
    user.cur_page = 1
    run_async(module.query_new_allowed_dialogs(123, SimpleNamespace(data="next")))

    keyboard = module.send_message.await_args.kwargs["reply_markup"].inline_keyboard
    assert keyboard[0][0].callback_data == str(module.PAGE_SIZE)
    assert module.send_message.await_args.kwargs["edit"] is True
    assert user.chosen_ids == {"0"}
    user.cur_page = 0
    run_async(module.query_new_allowed_dialogs(123, SimpleNamespace(data="prev")))
    keyboard = module.send_message.await_args.kwargs["reply_markup"].inline_keyboard
    assert keyboard[0][0].text.endswith("✅")
    assert module.send_message.await_args.kwargs["edit"] is True


@pytest.mark.parametrize("length", [0, 1, 6, 7, 12])
def test_page_wraparound_and_empty_lists(load_module, length):
    module = load_module("utils/helpers.py")
    user = SimpleNamespace(cur_page=0)
    count = max(1, (length + module.config_file["page_size"] - 1) // module.config_file["page_size"])

    module.prev_page(user, length)
    assert user.cur_page == count - 1
    module.next_page(user, length)
    assert user.cur_page == 0
