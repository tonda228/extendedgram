from time import monotonic
import os
from telegram.error import TelegramError
from pgvector import Vector
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telethon.tl.types import User, Channel

from bot import application
from server import select_host
from server.requests import send_request, request_embedding
from utils.classes import UserState
from features.preloading import reset_idle_timer
from database.dialogs import get_allowed_dialogs, update_dialog_priorities, delete_old_dialog_priorities, store_dialog
from database.messages import store_unsaved_messages, get_best_public_messages, get_best_private_messages
from bot.commands.menu import menu
from utils.config import config_file
from utils.helpers import get_message_info, get_full_chat_name, resend_processing_status, send_message
from utils.state import user_info
from utils.check_authentication import check_authentication

PAGE_SIZE = config_file["page_size"]

async def search_request(update: Update|None=None, context: ContextTypes.DEFAULT_TYPE|None=None, user_id: int|None=None, edit: bool=False, new_request: bool=True) -> None:
    if user_id is None:
        user_id = update.effective_user.id
    if not await check_authentication(user_id):
        return
    app_user = user_info[user_id]
    if new_request:
        app_user.dialogs = None
        app_user.chosen_ids = None
        app_user.cur_page = 0

    reset_idle_timer(user_id)

    if app_user.dialogs is None:
        app_user.dialogs = await get_allowed_dialogs(user_id)
        order_by = (lambda x: get_full_chat_name(x)) if app_user.order_by == "title" else None
        if order_by:
            app_user.dialogs.sort(key=order_by)
    dialogs = app_user.dialogs

    if dialogs is None or len(dialogs) == 0:
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

async def process_search_chat(user_id: int) -> None:
    user_id = user_id
    reset_idle_timer(user_id)

    given_dialogs = user_info[user_id].dialogs
    chosen_ids = user_info[user_id].chosen_ids
    if given_dialogs is None or chosen_ids is None:
        return

    if not chosen_ids:
        await send_message(user_id, "Choose at least one chat before confirming.")
        return
    try:
        indices = [int(index) for index in chosen_ids]
        if any(index < 0 or index >= len(given_dialogs) for index in indices):
            raise ValueError
    except ValueError:
        await send_message(user_id, "The chat list has changed. Please choose again.")
        await search_request(user_id=user_id)
        return
    queried_dialogs = [given_dialogs[index] for index in indices]
    for dialog in queried_dialogs:
        store_dialog(dialog, user_id, is_allowed=True)
        update_dialog_priorities(user_id, dialog)
    delete_old_dialog_priorities(user_id)

    markup = InlineKeyboardMarkup([[InlineKeyboardButton(text="Back", callback_data="back")]])
    await send_message(chat_id=user_id, text="Enter your query bellow:", reply_markup=markup)
    user_info[user_id].status = UserState.WAIT_FOR_SEARCH_TEXT
    user_info[user_id].dialogs = queried_dialogs

async def process_search_text(user_id: int, text: str) -> None:
    client = user_info[user_id].client
    queried_dialogs = user_info[user_id].dialogs
    if client is None or queried_dialogs is None:
        return

    selection = select_host(user_id)
    if selection is None:
        await send_message(user_id, "No host is available with a compatible embedding model.")
        await menu(user_id=user_id)
        return
    _, model = selection
    extra_model = model if model != os.getenv("EMBEDDING_MODEL") else None
    embedding = await request_embedding(user_id, text, model)
    if not embedding:
        await send_message(user_id, "There are no available hosts right now. Please try again later.")
        await menu(user_id=user_id)
        return

    best_messages = []
    message2 = None
    last_update = monotonic()
    last_percent = 0
    reset_idle_timer(user_id, False)
    try:
        message2 = await send_message(user_id, "░" * 40 + " 0%")
        for cur_status, dialog in enumerate(queried_dialogs, start=1):
            completed_download = await store_unsaved_messages(user_id, dialog, client, True, embedding_model=model)
            if not completed_download:
                await send_message(user_id, "Could not prepare messages. Please try again later.")
                await menu(user_id=user_id)
                return
            if isinstance(dialog[0].entity, Channel):
                best_messages += get_best_public_messages(dialog, embedding, user_id, extra_model)
            else:
                best_messages += get_best_private_messages(user_id, dialog, embedding, extra_model)
            percent = cur_status * 100 // len(queried_dialogs)
            if percent != last_percent and monotonic() - last_update >= 3:
                last_update = monotonic()
                if await resend_processing_status(user_id, message2, cur_status, len(queried_dialogs)):
                    last_update = monotonic()
                    last_percent = percent

        data = []
        for message in best_messages:
            get_message_info(data, message)

        result = await send_request(user_id, data, "search", text, embedding_model=model)
        if result is None:
            result = "Could not process the messages. Check your host and model, then try again."
        await send_message(user_id, result)
        await menu(user_id=user_id)
    finally:
        if message2 is not None:
            try:
                await message2.delete()
            except TelegramError:
                pass
        reset_idle_timer(user_id)
