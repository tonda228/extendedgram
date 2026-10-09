from pgvector import Vector
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telethon.tl.types import User, Channel

from bot import application
from server.requests import send_request
from utils.classes import UserState
from features.preloading import reset_idle_timer
from database.dialogs import get_allowed_dialogs, update_dialog_priorities, delete_old_dialog_priorities
from database.messages import store_unsaved_messages, get_best_public_messages, get_best_private_messages
from bot.commands.menu import menu
from llm.embeddings import create_embedding
from utils.config import config_file
from utils.helpers import get_message_info, get_full_chat_name, resend_processing_status, send_message
from utils.state import user_info
from utils.check_authentication import check_authentication

PAGE_SIZE = config_file["page_size"]

async def search_request(update: Update|None=None, context: ContextTypes.DEFAULT_TYPE|None=None, user_id=None, edit=False) -> None:
    if user_id is None:
        user_id = update.effective_user.id
    if not await check_authentication(user_id):
        return
    app_user = user_info[user_id]

    reset_idle_timer(user_id)

    if app_user.dialogs is None:
        app_user.dialogs = await get_allowed_dialogs(user_id)
        order_by = (lambda x: get_full_chat_name(x)) if app_user.order_by == "title" else None
        if order_by:
            app_user.dialogs.sort(key=order_by)
    dialogs = app_user.dialogs

    if len(dialogs) == 0:
        await send_message(chat_id=user_id, text="There are no dialogs or none are allowed.")
        await menu(user_id=user_id)
        return

    cur_page = app_user.cur_page
    start = cur_page * PAGE_SIZE
    end = start + PAGE_SIZE

    if app_user.chosen_ids is None:
        app_user.chosen_ids = set()
    ids = app_user.chosen_ids

    keyboard = []
    for index, dialog in enumerate(dialogs[start:end], start=start):
        dialog_name = dialog[0].title
        if dialog[1]:
            dialog_name += f"|{dialog[1].title}"
        dialog_text = dialog_name + ": " + ("✅" if str(index) in ids else "❌")
        keyboard.append([InlineKeyboardButton(text=dialog_text, callback_data=str(index))])

    if len(dialogs) > PAGE_SIZE:
        keyboard += [[InlineKeyboardButton(text="◀ Prev", callback_data="prev"),
                      InlineKeyboardButton(text="Next ▶️", callback_data="next")]]
    keyboard.append([InlineKeyboardButton(text="Confirm my choice", callback_data="confirm")])
    keyboard.append([InlineKeyboardButton(text="Back", callback_data="back")])
    markup = InlineKeyboardMarkup(keyboard)

    await send_message(user_id, "Choose in which chats you want to search.", reply_markup=markup, edit=edit)
    app_user.status = UserState.WAIT_FOR_SEARCH_CHAT
    app_user.dialogs = dialogs

async def process_search_chat(user_id) -> None:
    user_id = user_id
    reset_idle_timer(user_id)

    given_dialogs = user_info[user_id].dialogs
    reset_idle_timer(user_id)

    queried_dialogs = [given_dialogs[int(index)] for index in user_info[user_id].chosen_ids]
    for dialog in queried_dialogs:
        update_dialog_priorities(user_id, dialog)
    delete_old_dialog_priorities(user_id)

    markup = InlineKeyboardMarkup([[InlineKeyboardButton(text="Back", callback_data="back")]])
    await send_message(chat_id=user_id, text="Enter your query bellow:", reply_markup=markup)
    user_info[user_id].status = UserState.WAIT_FOR_SEARCH_TEXT
    user_info[user_id].dialogs = queried_dialogs

async def process_search_text(user_id, text: str):
    app_user = user_info[user_id]
    client = user_info[user_id].client
    queried_dialogs = user_info[user_id].dialogs
    embedding = await create_embedding(text)
    best_messages = []

    reset_idle_timer(user_id, reset=False)

    cur_status = 0
    message2 = await send_message(user_id, "|" + " " * 100 + "| 0%")
    for dialog in queried_dialogs:
        await store_unsaved_messages(user_id, dialog, client, True)
        if isinstance(dialog[0].entity, Channel):
            best_messages += get_best_public_messages(dialog, Vector(embedding), user_id)
        else:
            best_messages += get_best_private_messages(user_id, dialog, Vector(embedding))
        cur_status += 1
        await resend_processing_status(user_id, message2, cur_status, len(queried_dialogs))

    await application.bot.delete_message(user_id, message2.id)
    reset_idle_timer(user_id)

    data = []
    for message in best_messages:
        get_message_info(data, message)

    result = await send_request(user_id, data, "search", text)
    if result is None:
        result="There are no available hosts or hosts malfunctioning."

    await send_message(chat_id=user_id, text=result)
    await menu(user_id=user_id)
