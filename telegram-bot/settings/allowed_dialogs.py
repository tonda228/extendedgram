from html import escape

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from telethon.tl.types import User, Chat, Channel

from utils.classes import UserState
from database import cur, connection
from database.dialogs import get_allowed_dialogs, get_all_dialogs, store_dialog
from features.preloading import reset_idle_timer
from settings import settings
from utils.state import user_info
from utils.helpers import get_full_chat_name, get_topic_id

PAGE_SIZE = 6

async def display_allowed_dialogs(update: Update, context: ContextTypes.DEFAULT_TYPE, query):
    user_id = update.effective_user.id
    reset_idle_timer(user_id)

    if user_info[user_id].allow_all:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"All dialogs are allowed.")
    else:
        allowed_dialogs = await get_allowed_dialogs(user_id)
        if len(allowed_dialogs) == 0:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"No dialogs are allowed.")
        else:
            text = ""
            add_new_line = False
            for index, dialog in enumerate(allowed_dialogs, start=1):
                if add_new_line:
                    text += "\n"
                text += f"<b> {index}) {escape(get_full_chat_name(dialog))} </b>"
                add_new_line = True
            await context.bot.send_message(chat_id=update.effective_chat.id,
                                           text=text,
                                           parse_mode="HTML")

    keyboard = [
        [
            InlineKeyboardButton(text="Yes", callback_data="Yes"),
            InlineKeyboardButton(text="No", callback_data="No")
        ]
    ]
    msg = await context.bot.send_message(chat_id=update.effective_chat.id,
                                        text="Do you wish to change your choice?",
                                        reply_markup=InlineKeyboardMarkup(keyboard))
    user_info[user_id].message_id = msg.id
    user_info[user_id].status = UserState.WAIT_FOR_CHANGE_ALLOWED_DIALOGS_CONFIRMATION

async def display_new_allowed_dialogs_options(update: Update, context: ContextTypes.DEFAULT_TYPE, query):
    user_id = update.effective_user.id
    app_user = user_info[user_id]
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
    msg = await context.bot.edit_message_text(chat_id=update.effective_chat.id,
                                        message_id=query.message.message_id,
                                        text="Choose which dialogs you want to allow.\n"
                                             "Note: Previously manually allowed dialogs will stay that way if you don't unchoose them.\n",
                                        reply_markup=InlineKeyboardMarkup(keyboard))
    app_user.message_id = msg.id
    app_user.status = UserState.WAIT_FOR_NEW_ALLOWED_DIALOGS_OPTIONS_CHOICE
    app_user.allowed_dialogs_options = set() if app_user.allowed_dialogs_options is None else app_user.allowed_dialogs_options

async def query_new_allowed_dialogs_categories(update: Update, context: ContextTypes.DEFAULT_TYPE, query):
    user_id = update.effective_user.id
    app_user = user_info[user_id]
    all_dialogs = await get_all_dialogs(user_id)
    allowed_dialogs_set = app_user.allowed_dialogs_set if app_user.allowed_dialogs_set is not None else set()
    if {"choose", "channels", "chats", "users", "bots"}.issubset(app_user.allowed_dialogs_options):
        await change_allowed_dialogs(update, context)
        return

    initial_time = False
    if app_user.chosen_ids is None:
        initial_time = True
        app_user.chosen_ids = set()
    ids = app_user.chosen_ids

    reset_idle_timer(user_id)

    if "choose" not in app_user.allowed_dialogs_options:
        await change_allowed_dialogs(update, context)
        return

    given_dialogs = []

    save_channels = "channels" in app_user.allowed_dialogs_options
    save_chats = "chats" in app_user.allowed_dialogs_options
    save_users = "users" in app_user.allowed_dialogs_options
    save_bots = "bots" in app_user.allowed_dialogs_options

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

    msg = await context.bot.edit_message_text(chat_id=update.effective_chat.id,
                                              message_id=query.message.message_id,
                                              text="From which categories you want to select?",
                                              reply_markup=InlineKeyboardMarkup(keyboard))

    app_user.status = UserState.WAIT_FOR_ALLOWED_DIALOGS_CATEGORY_CHOICE
    app_user.message_id = msg.id
    app_user.dialogs = given_dialogs
    app_user.chosen_ids = ids
    app_user.allowed_dialogs_set = allowed_dialogs_set
    app_user.cur_page = 0


async def query_new_allowed_dialogs(update: Update, context: ContextTypes.DEFAULT_TYPE, query, ):
    user_id = update.effective_user.id
    app_user = user_info[user_id]

    all_dialogs = app_user.dialogs
    category = query.data if query.data not in ["prev", "next"] else app_user.last_category

    if app_user.chosen_ids is None:
        app_user.chosen_ids = set()
    ids = app_user.chosen_ids
    cur_page = app_user.cur_page
    start = cur_page * PAGE_SIZE
    end = start + PAGE_SIZE

    keyboard = []
    dialog_id = 0
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

        if not start <= dialog_id < end:
            dialog_id += 1
            continue

        dialog_name = dialog[0].title
        if dialog[1]:
            dialog_name += f"|{dialog[1].title}"

        dialog_text = dialog_name + ": " + ("✅" if str(index) in ids else "❌")
        keyboard.append([InlineKeyboardButton(text=dialog_text, callback_data=str(index))])
        dialog_id += 1


    if dialog_id > PAGE_SIZE:
        keyboard += [[InlineKeyboardButton(text="◀ Prev", callback_data="prev"),
                      InlineKeyboardButton(text="Next ▶️", callback_data="next")]]
    keyboard += [[InlineKeyboardButton(text="Confirm", callback_data="confirm")]]

    msg = await context.bot.edit_message_text(chat_id=update.effective_chat.id,
                                              message_id=query.message.message_id,
                                              text="Choose which dialogs you want to allow.",
                                              reply_markup=InlineKeyboardMarkup(keyboard))
    app_user.message_id = msg.id
    app_user.status = UserState.WAIT_FOR_ALLOWED_DIALOGS_MANUAL_CHOICE
    app_user.last_category = category
    app_user.category_dialogs_count = dialog_id

async def change_allowed_dialogs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    app_user = user_info[user_id]
    given_dialogs = app_user.dialogs or []
    all_dialogs = await get_all_dialogs(user_id)
    options = app_user.allowed_dialogs_options
    ids = {int(index) for index in app_user.chosen_ids} if app_user.chosen_ids is not None else set()
    allowed_dialogs_set = app_user.allowed_dialogs_set if app_user.allowed_dialogs_set is not None else set()

    reset_idle_timer(user_id)

    save_channels = "channels" in options
    save_chats = "chats" in options
    save_users = "users" in options
    save_bots = "bots" in options
    manually = "choose" in options

    new_allowed_dialogs = []

    # refactor this
    for dialog in all_dialogs:
        if save_channels and isinstance(dialog[0].entity, Channel):
            store_dialog(dialog, user_id, True)
            new_allowed_dialogs.append(dialog)
            allowed_dialogs_set.add((dialog[0].id, get_topic_id(dialog)))
        elif save_chats and isinstance(dialog[0].entity, Chat):
            store_dialog(dialog, user_id, True)
            new_allowed_dialogs.append(dialog)
            allowed_dialogs_set.add((dialog[0].id, get_topic_id(dialog)))
        elif save_users and isinstance(dialog[0].entity, User) and not dialog[0].entity.bot:
            store_dialog(dialog, user_id, True)
            new_allowed_dialogs.append(dialog)
            allowed_dialogs_set.add((dialog[0].id, get_topic_id(dialog)))
        elif save_bots and isinstance(dialog[0].entity, User) and dialog[0].entity.bot:
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
                app_user.allowed_dialogs_set.add((dialog[0].id, get_topic_id(dialog)))
            else:
                app_user.allow_all = False
                store_dialog(dialog, user_id, False)
                app_user.allowed_dialogs_set.discard((dialog[0].id, get_topic_id(dialog)))

    await context.bot.send_message(chat_id=update.effective_chat.id, text="Changes have been saved.")

    app_user.allowed_dialogs = new_allowed_dialogs
    app_user.allowed_dialogs_options = None
    app_user.chosen_ids = None

    if not app_user.allow_all:
        cur.execute("""
        UPDATE app_user
        SET allow_all = FALSE
        WHERE user_id = %s
        """, (user_id,))

    connection.commit()
    await settings(update, context)