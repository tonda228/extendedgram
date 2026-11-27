from telegram import CallbackQuery
from telethon.tl.custom import Dialog
from telethon.tl.types import ForumTopic

from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telethon.tl.types import User, Chat, Channel

from utils.classes import UserState
from database import cur, connection
from database.dialogs import get_allowed_dialogs, get_all_dialogs, store_dialog
from features.preloading import reset_idle_timer
from settings import settings
from utils.config import config_file
from utils.state import user_info
from utils.helpers import get_full_chat_name, get_topic_id, send_message

PAGE_SIZE = config_file["page_size"]

async def display_allowed_dialogs(user_id: int, edit: bool = False, new_request: bool = True) -> None:
    app_user = user_info[user_id]
    reset_idle_timer(user_id)

    if new_request or app_user.dialogs is None:
        app_user.dialogs = list(await get_allowed_dialogs(user_id))
        if app_user.order_by == "title":
            app_user.dialogs.sort(key=get_full_chat_name)
        app_user.cur_page = 0
    dialogs = app_user.dialogs
    pages_count = max(1, (len(dialogs) + PAGE_SIZE - 1) // PAGE_SIZE)
    app_user.cur_page %= pages_count
    start = app_user.cur_page * PAGE_SIZE
    lines = ["All dialogs are allowed." if app_user.allow_all else "Allowed dialogs:"]
    if not dialogs:
        lines.append("No dialogs are available." if app_user.allow_all else "No dialogs are allowed.")
    for index, dialog in enumerate(dialogs[start:start + PAGE_SIZE], start=start + 1):
        lines.append(f"<b>{index}) {escape(get_full_chat_name(dialog)[:180])}</b>")
    lines += [f"\nPage {app_user.cur_page + 1}/{pages_count}", "Do you wish to change your choice?"]

    keyboard = [
        [
            InlineKeyboardButton(text="Yes", callback_data="Yes"),
            InlineKeyboardButton(text="No", callback_data="No")
        ]
    ]
    if pages_count > 1:
        keyboard.insert(0, [InlineKeyboardButton("◀ Prev", callback_data="prev"),
                            InlineKeyboardButton("Next ▶️", callback_data="next")])
    keyboard.append([InlineKeyboardButton("Back", callback_data="back")])
    await send_message(user_id, "\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard),
                       parse_mode="HTML", edit=edit)
    app_user.status = UserState.WAIT_FOR_CHANGE_ALLOWED_DIALOGS_CONFIRMATION

async def display_new_allowed_dialogs_options(user_id: int, query: CallbackQuery) -> None:
    app_user = user_info[user_id]
    if app_user.status == UserState.WAIT_FOR_CHANGE_ALLOWED_DIALOGS_CONFIRMATION:
        app_user.chosen_ids = None
        app_user.dialogs = None
        app_user.allowed_dialogs_options = None
        app_user.cur_page = 0
        app_user.last_category = None
    options = app_user.allowed_dialogs_options if app_user.allowed_dialogs_options is not None else set()
    keyboard = [
        [InlineKeyboardButton(text="Allow all channels: " + ("✅" if "channels" in options else "❌"), callback_data="channels")],
        [InlineKeyboardButton(text="Allow all chats/groups: " + ("✅" if "chats" in options else "❌"), callback_data="chats")],
        [InlineKeyboardButton(text="Allow all user chats: " + ("✅" if  "users" in options else "❌"), callback_data="users")],
        [InlineKeyboardButton(text="Allow all bots: " + ("✅" if "bots" in options else "❌"), callback_data="bots")],
        [InlineKeyboardButton(text="Choose the rest manually: " + ("✅" if "choose" in options else "❌"), callback_data="choose")],
        [InlineKeyboardButton(text="Confirm", callback_data="confirm")],
        [InlineKeyboardButton(text="Back", callback_data="back")]
    ]
    await send_message(user_id,
                       "Choose which dialogs you want to allow.\nNote: Previously manually allowed dialogs will stay that way if you don't unchoose them.\n",
                       reply_markup=InlineKeyboardMarkup(keyboard),
                       edit=True)
    app_user.status = UserState.WAIT_FOR_NEW_ALLOWED_DIALOGS_OPTIONS_CHOICE
    app_user.allowed_dialogs_options = set() if app_user.allowed_dialogs_options is None else app_user.allowed_dialogs_options

async def query_new_allowed_dialogs_categories(user_id: int, query: CallbackQuery) -> None:
    app_user = user_info[user_id]
    all_dialogs = await get_all_dialogs(user_id)
    allowed_dialogs_set = app_user.allowed_dialogs_set if app_user.allowed_dialogs_set is not None else set()
    if {"choose", "channels", "chats", "users", "bots"}.issubset(app_user.allowed_dialogs_options):
        await change_allowed_dialogs(user_id)
        return

    initial_time = False
    if app_user.chosen_ids is None:
        initial_time = True
        app_user.chosen_ids = set()
    ids = app_user.chosen_ids

    reset_idle_timer(user_id)

    if "choose" not in app_user.allowed_dialogs_options:
        await change_allowed_dialogs(user_id)
        return

    given_dialogs = []

    save_channels = "channels" in app_user.allowed_dialogs_options
    save_chats = "chats" in app_user.allowed_dialogs_options
    save_users = "users" in app_user.allowed_dialogs_options
    save_bots = "bots" in app_user.allowed_dialogs_options

    if app_user.order_by == "title":
        all_dialogs.sort(key=lambda x: get_full_chat_name(x))

    index = 0
    for dialog in all_dialogs:
        if save_channels and isinstance(dialog[0].entity, Channel):
            continue
        if save_chats and isinstance(dialog[0].entity, Chat):
            continue
        if save_users and isinstance(dialog[0].entity, User) and not dialog[0].entity.bot:
            continue
        if save_bots and isinstance(dialog[0].entity, User) and dialog[0].entity.bot:
            continue
        if initial_time and (dialog[0].id, get_topic_id(dialog)) in allowed_dialogs_set:
            ids.add(str(index))
        given_dialogs.append(dialog)
        index += 1

    keyboard = []
    if not save_channels:
        keyboard.append([InlineKeyboardButton(text="Choose channels", callback_data="channels")])
    if not save_chats:
        keyboard.append([InlineKeyboardButton(text="Choose chats/groups", callback_data="chats")])
    if not save_users:
        keyboard.append([InlineKeyboardButton(text="Choose user chats", callback_data="users")])
    if not save_bots:
        keyboard.append([InlineKeyboardButton(text="Choose bots", callback_data="bots")])

    keyboard += [[InlineKeyboardButton(text="Confirm", callback_data="confirm")],
                [InlineKeyboardButton(text="Back", callback_data="back")]]

    await send_message(chat_id=user_id, text="From which categories you want to select?", reply_markup=InlineKeyboardMarkup(keyboard))
    app_user.status = UserState.WAIT_FOR_ALLOWED_DIALOGS_CATEGORY_CHOICE
    app_user.dialogs = given_dialogs
    app_user.chosen_ids = ids
    app_user.allowed_dialogs_set = allowed_dialogs_set
    app_user.cur_page = 0


async def query_new_allowed_dialogs(user_id: int, query: CallbackQuery, ) -> None:
    app_user = user_info[user_id]

    all_dialogs = app_user.dialogs
    category = query.data if query.data not in ["prev", "next"] else app_user.last_category

    if app_user.chosen_ids is None:
        app_user.chosen_ids = set()
    ids = app_user.chosen_ids
    if query.data not in ("prev", "next"):
        app_user.cur_page = 0
    visible_dialogs = []
    for index, dialog in enumerate(all_dialogs):
        if category == "channels" and not isinstance(dialog[0].entity, Channel):
            continue
        if category == "chats" and not isinstance(dialog[0].entity, Chat):
            continue
        if category == "users" and not (isinstance(dialog[0].entity, User) and not dialog[0].entity.bot):
            continue
        if category == "bots" and not (isinstance(dialog[0].entity, User) and dialog[0].entity.bot):
            continue

        if isinstance(dialog[0].entity, User) and dialog[0].entity.deleted:
            continue
        if isinstance(dialog[0].entity, Chat) and (dialog[0].entity.left or dialog[0].entity.deactivated):
            continue
        if isinstance(dialog[0].entity, Channel) and (dialog[0].entity.left or dialog[0].entity.restricted):
            continue

        visible_dialogs.append((index, dialog))

    pages_count = max(1, (len(visible_dialogs) + PAGE_SIZE - 1) // PAGE_SIZE)
    app_user.cur_page %= pages_count
    start = app_user.cur_page * PAGE_SIZE
    keyboard = []
    for index, dialog in visible_dialogs[start:start + PAGE_SIZE]:
        dialog_name = get_full_chat_name(dialog)
        dialog_text = dialog_name + ": " + ("✅" if str(index) in ids else "❌")
        keyboard.append([InlineKeyboardButton(text=dialog_text, callback_data=str(index))])


    if pages_count > 1:
        keyboard += [[InlineKeyboardButton(text="◀ Prev", callback_data="prev"),
                      InlineKeyboardButton(text="Next ▶️", callback_data="next")]]
    keyboard += [[InlineKeyboardButton(text="Confirm", callback_data="confirm")]]
    keyboard += [[InlineKeyboardButton(text="Back", callback_data="back")]]

    await send_message(chat_id=user_id, text=f"Choose which dialogs you want to allow.\nPage {app_user.cur_page + 1}/{pages_count}",
                       reply_markup=InlineKeyboardMarkup(keyboard), edit=query.data in ("prev", "next"))
    app_user.status = UserState.WAIT_FOR_ALLOWED_DIALOGS_MANUAL_CHOICE
    app_user.last_category = category
    app_user.category_dialogs_count = len(visible_dialogs)

def is_suitable(dialog: tuple[Dialog, ForumTopic | None], options: set[str]) -> bool:
    save_channels = "channels" in options
    save_chats = "chats" in options
    save_users = "users" in options
    save_bots = "bots" in options

    if save_channels and isinstance(dialog[0].entity, Channel):
        return True
    if save_chats and isinstance(dialog[0].entity, Chat):
        return True
    if save_users and isinstance(dialog[0].entity, User) and not dialog[0].entity.bot:
        return True
    if save_bots and isinstance(dialog[0].entity, User) and dialog[0].entity.bot:
        return True
    return False

async def change_allowed_dialogs(user_id: int) -> None:
    app_user = user_info[user_id]
    given_dialogs = app_user.dialogs or []
    all_dialogs = await get_all_dialogs(user_id)
    options = app_user.allowed_dialogs_options if app_user.allowed_dialogs_options is not None else set()
    ids = {int(index) for index in app_user.chosen_ids} if app_user.chosen_ids is not None else set()
    allowed_dialogs_set = app_user.allowed_dialogs_set if app_user.allowed_dialogs_set is not None else set()

    manually = "choose" in options
    new_allowed_dialogs = []

    for dialog in all_dialogs:
        if is_suitable(dialog, options):
            store_dialog(dialog, user_id, True)
            new_allowed_dialogs.append(dialog)
            allowed_dialogs_set.add((dialog[0].id, get_topic_id(dialog)))
        elif not manually:
            app_user.allow_all = False
            store_dialog(dialog, user_id, False)
            allowed_dialogs_set.discard((dialog[0].id, get_topic_id(dialog)))


    if manually:
        for index, dialog in enumerate(given_dialogs):
            if index in ids:
                store_dialog(dialog, user_id, True)
                new_allowed_dialogs.append(dialog)
                allowed_dialogs_set.add((dialog[0].id, get_topic_id(dialog)))
            else:
                app_user.allow_all = False
                store_dialog(dialog, user_id, False)
                allowed_dialogs_set.discard((dialog[0].id, get_topic_id(dialog)))


    reset_idle_timer(user_id)

    app_user.allowed_dialogs = new_allowed_dialogs
    app_user.allowed_dialogs_set = allowed_dialogs_set
    app_user.allowed_dialogs_options = None
    app_user.chosen_ids = None

    if not app_user.allow_all:
        cur.execute("""
        UPDATE app_user
        SET allow_all = FALSE
        WHERE user_id = %s
        """, (user_id,))

    connection.commit()
    await settings(user_id=user_id, notice="Changes have been saved.")
