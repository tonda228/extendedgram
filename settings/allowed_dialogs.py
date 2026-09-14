from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from telethon.tl.types import User, Chat, Channel

from classes import UserState
from database import cur, connection
from database.dialogs import get_allowed_dialogs, get_all_dialogs, store_dialog
from settings import settings
from state import user_info
from utils import reset_idle_timer, update_inline_keyboard, get_full_chat_name


# I can save allowed dialogs in runtime memory
# find a way to check if dialog was added and update allow_all
# add one more layer for choosing
# also don't always jump straight to menu after /back

async def display_allowed_dialogs(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
                text += f"<b>{index}) {get_full_chat_name(dialog)}</b>"
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
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Do you wish to change your choice?", reply_markup=InlineKeyboardMarkup(keyboard))
    user_info[user_id].status = UserState.WAIT_FOR_CHANGE_ALLOWED_DIALOGS_CONFIRMATION

async def display_new_allowed_dialogs_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(text="Allow channels: ❌", callback_data="channels")],
        [InlineKeyboardButton(text="Allow chats/groups: ❌", callback_data="chats")],
        [InlineKeyboardButton(text="Allow user chats: ❌", callback_data="users")],
        [InlineKeyboardButton(text="Allow bots: ❌", callback_data="bots")],
        [InlineKeyboardButton(text="Choose manually: ❌", callback_data="choose")],
        [InlineKeyboardButton(text="Confirm", callback_data="confirm")]
    ]
    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text="Choose which dialogs you want to allow.\n"
                                        "Note: if you select any of the groups (e.g 'Allow channels') and "
                                        "'Choose manually, you will choose dialogs that doesnt belong to any "
                                        "of selected groups.",
                                   reply_markup=InlineKeyboardMarkup(keyboard))
    user_info[update.effective_user.id].status = UserState.WAIT_FOR_NEW_ALLOWED_DIALOGS_OPTIONS_CHOICE
    user_info[update.effective_user.id].allowed_dialogs_options = set()

async def flip_allowed_dialogs_options_state(update: Update, context: ContextTypes.DEFAULT_TYPE, query):
    keyboard = query.message.reply_markup.inline_keyboard
    new_markup = update_inline_keyboard(keyboard, query.data, user_id=update.effective_user.id, save_to="allowed_dialogs_options")

    await context.bot.edit_message_reply_markup(chat_id=update.effective_chat.id,
                                                message_id=query.message.message_id,
                                                reply_markup=new_markup)

async def query_new_allowed_dialogs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    app_user = user_info[user_id]
    all_dialogs = await get_all_dialogs(user_id)

    reset_idle_timer(user_id)

    keyboard = []

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
        dialog_name = dialog[0].title
        if dialog[1]:
            dialog_name += f"|{dialog[1].title}"
        dialog_text = dialog_name + ": ❌"
        keyboard.append([InlineKeyboardButton(text=dialog_text, callback_data=str(index))])
        given_dialogs.append(dialog)
        index += 1

    keyboard.append([InlineKeyboardButton(text="Confirm my choice", callback_data="confirm")])
    keyboard.append([InlineKeyboardButton(text="Back", callback_data="back")])
    markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text="Choose which dialogs you want to allow.",
                                   reply_markup=markup)

    app_user.status = UserState.WAIT_FOR_ALLOWED_DIALOGS_MANUAL_CHOICE
    app_user.dialogs = given_dialogs
    user_info[update.effective_user.id].chosen_ids = set()

# add some states maybe

async def flip_new_allowed_dialogs_state(update: Update, context: ContextTypes.DEFAULT_TYPE, query):
    keyboard = query.message.reply_markup.inline_keyboard
    new_markup = update_inline_keyboard(keyboard, query.data, user_id=update.effective_user.id, save_to="chosen_ids")

    await context.bot.edit_message_reply_markup(chat_id=update.effective_chat.id,
                                                message_id=query.message.message_id,
                                                reply_markup=new_markup)

async def change_allowed_dialogs(update: Update, context: ContextTypes.DEFAULT_TYPE, text):
    user_id = update.effective_user.id
    app_user = user_info[user_id]
    given_dialogs = app_user.dialogs or []
    all_dialogs = await get_all_dialogs(user_id)
    options = app_user.allowed_dialogs_options
    ids = {int(index) for index in app_user.chosen_ids} if  app_user.chosen_ids else set()

    # finish this

    reset_idle_timer(user_id)

    save_channels = "channels" in options
    save_chats = "chats" in options
    save_users = "users" in options
    save_bots = "bots" in options
    manually = "choose" in options

    for dialog in all_dialogs:
        if save_channels and isinstance(dialog[0].entity, Channel):
            store_dialog(dialog, user_id, True)
        elif save_chats and isinstance(dialog[0].entity, Chat):
            store_dialog(dialog, user_id, True)
        elif save_users and isinstance(dialog[0].entity, User) and not dialog[0].entity.bot:
            store_dialog(dialog, user_id, True)
        elif save_bots and isinstance(dialog[0].entity, User) and dialog[0].entity.bot:
            store_dialog(dialog, user_id, True)
        elif not manually:
            app_user.allow_all = False
            store_dialog(dialog, user_id, False)



    for index, dialog in enumerate(given_dialogs):
        if index in ids:
            store_dialog(dialog, user_id, True)
        else:
            app_user.allow_all = False
            store_dialog(dialog, user_id, False)

    await context.bot.send_message(chat_id=update.effective_chat.id, text="Changes have been saved.")

    if not app_user.allow_all:
        cur.execute("""
        UPDATE app_user
        SET allow_all = FALSE
        WHERE user_id = %s
        """, (user_id,))

    # delete dialogs that became restricted?
    connection.commit()
    await settings(update, context)