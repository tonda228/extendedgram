import os

from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes

from bot.commands.requests import resolve_request
from utils.auth import process_phone_number, process_code, process_password
from bot.commands.logout import log_out_request, log_out_confirmation
from utils.classes import UserState
from database.users import flip_user_state, send_user_request
from bot.commands.menu import menu
from features.preloading import reset_idle_timer

from features.search import search_request, process_search_chat, process_search_text

from settings import settings
from settings.allowed_dialogs import display_allowed_dialogs, change_allowed_dialogs, \
    display_new_allowed_dialogs_options, query_new_allowed_dialogs_categories, query_new_allowed_dialogs, PAGE_SIZE
from settings.history_size import display_history_size, change_history_size, query_new_history_size
from utils.helpers import next_page, prev_page
from utils.state import user_info
from features.summarize import summarize_request, process_summarize_query
from utils.check_authentication import check_authentication
from utils.keyboard import send_updated_inline_keyboard

# Telegram BOT
BOT_API_TOKEN = os.environ["BOT_API_TOKEN"]
application = ApplicationBuilder().token(BOT_API_TOKEN).concurrent_updates(True).build()

async def process_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    query = update.callback_query

    if user_id not in user_info:
        if query.data == "yes":
            await send_user_request(update.effective_user, context.bot)
        await query.answer()
        return

    if not await check_authentication(update, context):
        return

    app_user = user_info[user_id]
    reset_idle_timer(user_id)
    await query.answer()

    if query.data == "summarize":
        app_user.cur_page = 0
        app_user.dialogs = None
        await summarize_request(update, context)
    elif query.data == "search":
        app_user.cur_page = 0
        app_user.chosen_ids = None
        app_user.dialogs = None
        await search_request(update, context)
    elif query.data == "settings":
        await settings(update, context)

    if app_user.status == UserState.WAIT_FOR_REQUEST_ANSWER:
        new_user_id = int(query.data.split(" ")[-1])
        await resolve_request(update, context, user_id, new_user_id, query.data.startswith("accept"))
        await query.delete_message()
        return
    if query.message.id != app_user.message_id:
        return

    # summarize
    elif app_user.status == UserState.WAIT_FOR_SUMMARIZE_CHAT:
        if query.data == "back":
            await query.delete_message()
            app_user.status = UserState.AUTHENTICATED
        elif query.data == "prev":
            prev_page(app_user, len(app_user.dialogs))
            await summarize_request(update, context, query, True)
        elif query.data == "next":
            next_page(app_user, len(app_user.dialogs))
            await summarize_request(update, context, query, True)
        else:
            await process_summarize_query(update, context, query)

    elif app_user.status == UserState.WAIT_FOR_READ:
        if query.data == "Yes":
            await app_user.client.send_read_acknowledge(app_user.last_read[0], clear_mentions=True, clear_reactions=True)
        await query.delete_message()
        await menu(update, context)

    # search
    elif app_user.status == UserState.WAIT_FOR_SEARCH_CHAT:
        if query.data == "confirm":
            await process_search_chat(update, context)
        elif query.data == "back":
            await query.delete_message()
            app_user.status = UserState.AUTHENTICATED
        elif query.data == "prev":
            prev_page(app_user, len(app_user.dialogs))
            await search_request(update, context, query, True)
        elif query.data == "next":
            next_page(app_user, len(app_user.dialogs))
            await search_request(update, context, query, True)
        elif query.data == "back":
            await query.delete_message()
            app_user.status = UserState.AUTHENTICATED
            await menu(update, context)
        else:
            await send_updated_inline_keyboard(update, context, query, save_to=app_user.chosen_ids)

    elif app_user.status == UserState.WAIT_FOR_SEARCH_TEXT:
        if query.data == "back":
            await query.delete_message()

    # allowed_dialogs
    elif app_user.status == UserState.WAIT_FOR_CHANGE_ALLOWED_DIALOGS_CONFIRMATION:
        if query.data == "Yes":
            await display_new_allowed_dialogs_options(update, context, query)
        else:
            await query.delete_message()
            await settings(update, context)

    elif app_user.status == UserState.WAIT_FOR_NEW_ALLOWED_DIALOGS_OPTIONS_CHOICE:
        if query.data == "confirm":
            if "choose" in user_info[update.effective_user.id].allowed_dialogs_options:
                await query_new_allowed_dialogs_categories(update, context, query)
            else:
                await query.delete_message()
                await change_allowed_dialogs(update, context)
        elif query.data == "back":
            await query.delete_message()
            await settings(update, context)
        else:
            user_id = update.effective_user.id
            app_user = user_info[user_id]
            await send_updated_inline_keyboard(update, context, query, save_to=app_user.allowed_dialogs_options)

    elif app_user.status == UserState.WAIT_FOR_ALLOWED_DIALOGS_CATEGORY_CHOICE:
        if query.data == "confirm":
            await query.delete_message()
            await change_allowed_dialogs(update, context)
        elif query.data == "back":
            await display_new_allowed_dialogs_options(update, context, query)
        else:
            await query_new_allowed_dialogs(update, context, query)

    elif app_user.status == UserState.WAIT_FOR_ALLOWED_DIALOGS_MANUAL_CHOICE:
        if query.data == "confirm":
            await query_new_allowed_dialogs_categories(update, context, query)
        elif query.data == "prev":
            prev_page(app_user, app_user.category_dialogs_count)
            await query_new_allowed_dialogs(update,context, query)
        elif query.data == "next":
            next_page(app_user, app_user.category_dialogs_count)
            await query_new_allowed_dialogs(update,context, query)
        elif query.data == "back":
            await display_new_allowed_dialogs_options(update, context, query)
        else:
            user_id = update.effective_user.id
            app_user = user_info[user_id]
            await send_updated_inline_keyboard(update, context, query, save_to=app_user.chosen_ids)

    # log_out
    elif app_user.status == UserState.WAIT_FOR_LOG_OUT_CONFIRMATION:
        await query.delete_message()
        if query.data == "Yes":
            await log_out_confirmation(update, context)
        else:
            app_user.status = UserState.WAIT_FOR_SETTINGS_CHOICE

    # settings
    elif app_user.status == UserState.WAIT_FOR_SETTINGS_CHOICE:
        if query.data in ["allow_all", "set_read_after_summary", "preloading"]:
            column = query.data
            flip_user_state(user_id, column)

            await send_updated_inline_keyboard(update, context, query)
        elif query.data == "allowed_dialogs":
            await display_allowed_dialogs(update, context, query)
        elif query.data == "history_size":
            await display_history_size(update, context)
        elif query.data == "log_out":
            await log_out_request(update, context)
        elif query.data == "back":
            await menu(update, context)

    # history_size
    elif app_user.status == UserState.WAIT_FOR_HISTORY_SIZE_CHANGE_CONFIRMATION:
        if query.data == "Yes":
            await query_new_history_size(update, context)
        else:
            await settings(update, context)
        await query.delete_message()


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
    elif status == UserState.WAIT_FOR_SEARCH_TEXT:
        await process_search_text(update, context, text)
    elif status == UserState.WAIT_FOR_NEW_HISTORY_SIZE:
        await change_history_size(update, context, text)