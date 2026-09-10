import os

from telegram import Update, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, ContextTypes

from auth import process_phone_number, process_code, process_password
from bot.commands.logout import log_out_request
from classes import UserState
from database.users import flip_user_state
from bot.commands.menu import menu
from features.preloading import reset_idle_timer

from features.search import search_request, process_search_chat, process_search_text

from settings import settings
from settings.allowed_dialogs import query_new_allowed_dialogs, display_allowed_dialogs, change_allowed_dialogs
from settings.history_size import display_history_size, change_history_size, query_new_history_size
from state import user_info
from features.summarize import summarize_request, process_summarize_query
from utils import check_authentication, update_inline_keyboard

# Telegram BOT
BOT_API_TOKEN = os.environ["BOT_API_TOKEN"]
application = ApplicationBuilder().token(BOT_API_TOKEN).concurrent_updates(True).build()

async def process_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_authentication(update, context):
        return

    user_id = update.effective_user.id
    app_user = user_info[user_id]
    query = update.callback_query

    reset_idle_timer(user_id)

    await query.answer()

    if app_user.status == UserState.WAIT_FOR_READ:
        if query.data == "Yes":
            await app_user.client.send_read_acknowledge(app_user.last_read[0], clear_mentions=True, clear_reactions=True)
        await query.delete_message()
        await menu(update, context)

    elif app_user.status == UserState.WAIT_FOR_CHANGE_ALLOWED_DIALOGS_CONFIRMATION:
        if query.data == "Yes":
            await query_new_allowed_dialogs(update, context)
        else:
            await settings(update, context)
        await query.delete_message()

    elif app_user.status == UserState.WAIT_FOR_MENU_CHOICE:
        if query.data == "summarize":
            await summarize_request(update, context)
        elif query.data == "search":
            await search_request(update, context)
        elif query.data == "settings":
            await settings(update, context)

    elif app_user.status == UserState.WAIT_FOR_SETTINGS_CHOICE:
        # change it later
        if query.data in ["allow_all", "set_read_after_summary", "preloading"]:
            column = query.data
            flip_user_state(user_id, column)

            new_state = getattr(user_info[user_id], column)
            keyboard = query.message.reply_markup.inline_keyboard
            new_markup = update_inline_keyboard(keyboard, column, new_state)
            await context.bot.edit_message_reply_markup(chat_id=update.effective_chat.id,
                                                        message_id=query.message.message_id,
                                                        reply_markup=new_markup)
        elif query.data == "allowed_dialogs":
            # await query.delete_message()
            await display_allowed_dialogs(update, context)
        elif query.data == "history_size":
            await display_history_size(update, context)
        elif query.data == "log_out":
            await log_out_request(update, context)
        elif query.data == "back":
            # await query.delete_message()
            await menu(update, context)

    elif app_user.status == UserState.WAIT_FOR_HISTORY_SIZE_CHANGE_CONFIRMATION:
        if query.data == "Yes":
            await query_new_history_size(update, context)
        else:
            await settings(update, context)
        await query.delete_message()
    elif query.data == "back":
        await menu(update, context)


async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in user_info or user_info[update.effective_user.id].status == UserState.LOGGED_OUT:
        return

    text = update.message.text

    status = user_info[update.effective_user.id].status
    if status == UserState.WAIT_FOR_PHONE_NUMBER:
        await process_phone_number(update, context, text)
    elif status == UserState.WAIT_FOR_CODE:
        await process_code(update, context, text)
    elif status == UserState.WAIT_FOR_PASSWORD:
        await process_password(update, context, text)
    elif status == UserState.WAIT_FOR_SUMMARIZE_CHAT:
        await process_summarize_query(update, context, text)
    elif status == UserState.WAIT_FOR_SEARCH_CHAT:
        await process_search_chat(update, context, text)
    elif status == UserState.WAIT_FOR_SEARCH_TEXT:
        await process_search_text(update, context, text)
    elif status == UserState.WAIT_FOR_ALLOWED_DIALOGS_CHOICE:
        await change_allowed_dialogs(update, context, text)
    elif status == UserState.WAIT_FOR_NEW_HISTORY_SIZE:
        await change_history_size(update, context, text)