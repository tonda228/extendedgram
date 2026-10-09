from telegram import Update
from telegram.ext import ContextTypes
from telethon import functions

from bot.commands.logout import log_out_confirmation, log_out_request
from bot.commands.menu import menu
from utils.classes import UserState, LoginStage
from database.users import flip_user_state, update_ordering

from database.groups import process_password_confirmation, query_password_confirmation, \
    query_new_group_password, join_group, check_group_password, create_group_request, join_group_request, \
    display_group_info
from utils.auth import process_phone_number, process_code, process_password, process_bot_password, send_one_time_code, \
    check_bot_password, process_bot_password_confirmation

from features.preloading import reset_idle_timer
from features.search import search_request, process_search_chat, process_search_text
from features.summarize import summarize_request, process_summarize_query

from settings import settings
from settings.allowed_dialogs import display_allowed_dialogs, change_allowed_dialogs, \
    display_new_allowed_dialogs_options, query_new_allowed_dialogs_categories, query_new_allowed_dialogs
from settings.history_size import display_history_size, change_history_size, query_new_history_size

from utils.helpers import next_page, prev_page
from utils.state import user_info, uninitialized_users
from utils.check_authentication import check_authentication, offer_host_type, query_phone_number
from utils.keyboard import send_updated_inline_keyboard

async def process_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    query = update.callback_query
    await query.answer()

    if user_id in uninitialized_users:
        uninitialized_user = uninitialized_users[user_id]
        if uninitialized_user.status == LoginStage.AWAIT_ROLE_CHOICE:
            uninitialized_user.is_host = (query.data == "host")
            if uninitialized_user.is_host:
                await offer_host_type(user_id)
            else:
                uninitialized_user.in_group = True
                await query_phone_number(user_id)
        elif uninitialized_user.status == LoginStage.AWAIT_ROLE_TYPE_CHOICE:
            uninitialized_user.in_group = (query.data == "group")
            await query_phone_number(user_id)
        elif uninitialized_user.status == LoginStage.AWAIT_GROUP_CHOICE:
            if query.data == "create":
                await create_group_request(user_id)
            elif query.data == "join":
                await join_group_request(user_id)
        elif uninitialized_user.status == LoginStage.AWAIT_IS_READY:
            await send_one_time_code(user_id)
        return

    if not await check_authentication(user_id):
        return

    app_user = user_info[user_id]
    reset_idle_timer(user_id)

    # menu
    if query.data == "summarize":
        app_user.cur_page = 0
        app_user.dialogs = None
        await summarize_request(user_id=user_id, edit=True)
        return
    elif query.data == "search":
        app_user.cur_page = 0
        app_user.chosen_ids = None
        app_user.dialogs = None
        await search_request(user_id=user_id)
        return
    elif query.data == "settings":
       await settings(user_id=user_id, edit=True)
       return

    if app_user.last_message and query.message.id != app_user.last_message.message_id:
        return


    # summarize
    elif app_user.status == UserState.WAIT_FOR_SUMMARIZE_CHAT:
        if query.data == "back":
            await query.delete_message()
            app_user.status = UserState.AUTHENTICATED
        elif query.data == "prev":
            prev_page(app_user, len(app_user.dialogs))
            await summarize_request(user_id=user_id, edit=True)
        elif query.data == "next":
            next_page(app_user, len(app_user.dialogs))
            await summarize_request(user_id=user_id, edit=True)
        else:
            await process_summarize_query(user_id, query)

    elif app_user.status == UserState.WAIT_FOR_READ:
        print("couldn't get here, :(")
        if query.data == "Yes":
            if app_user.last_read[1] is None:
                await app_user.client.send_read_acknowledge(app_user.last_read[0], clear_mentions=True, clear_reactions=True)
            else:
                await app_user.client(
                    functions.messages.ReadDiscussionRequest(
                        peer=app_user.last_read[0].entity,
                        msg_id=app_user.last_read[1].id,
                        read_max_id=app_user.last_read[1].top_message
                    )
                )
        await query.delete_message()
        await menu(user_id=user_id)

    # search
    elif app_user.status == UserState.WAIT_FOR_SEARCH_CHAT:
        if query.data == "confirm":
            await process_search_chat(user_id)
        elif query.data == "back":
            await query.delete_message()
            app_user.status = UserState.AUTHENTICATED
        elif query.data == "prev":
            prev_page(app_user, len(app_user.dialogs))
            await search_request(user_id=user_id,edit=True)
        elif query.data == "next":
            next_page(app_user, len(app_user.dialogs))
            await search_request(user_id=user_id, edit=True)
        elif query.data == "back":
            await query.delete_message()
            app_user.status = UserState.AUTHENTICATED
            await menu(user_id=user_id)
        else:
            await send_updated_inline_keyboard(user_id, query, save_to=app_user.chosen_ids)

    elif app_user.status == UserState.WAIT_FOR_SEARCH_TEXT:
        if query.data == "back":
            await query.delete_message()

    # allowed_dialogs
    elif app_user.status == UserState.WAIT_FOR_CHANGE_ALLOWED_DIALOGS_CONFIRMATION:
        if query.data == "Yes":
            await display_new_allowed_dialogs_options(user_id, query)
        else:
            await query.delete_message()
            await settings(user_id=user_id)

    elif app_user.status == UserState.WAIT_FOR_NEW_ALLOWED_DIALOGS_OPTIONS_CHOICE:
        if query.data == "confirm":
            if "choose" in user_info[update.effective_user.id].allowed_dialogs_options:
                await query_new_allowed_dialogs_categories(user_id, query)
            else:
                await query.delete_message()
                await change_allowed_dialogs(user_id)
        elif query.data == "back":
            await query.delete_message()
            await settings(user_id=user_id)
        else:
            user_id = update.effective_user.id
            app_user = user_info[user_id]
            await send_updated_inline_keyboard(user_id, query, save_to=app_user.allowed_dialogs_options)
    
    elif app_user.status == UserState.WAIT_FOR_ALLOWED_DIALOGS_CATEGORY_CHOICE:
        if query.data == "confirm":
            await query.delete_message()
            await change_allowed_dialogs(user_id)
        elif query.data == "back":
            await display_new_allowed_dialogs_options(user_id, query)
        else:
            await query_new_allowed_dialogs(user_id, query)

    elif app_user.status == UserState.WAIT_FOR_ALLOWED_DIALOGS_MANUAL_CHOICE:
        if query.data == "confirm":
            await query_new_allowed_dialogs_categories(user_id, query)
        elif query.data == "prev":
            prev_page(app_user, app_user.category_dialogs_count)
            await query_new_allowed_dialogs(user_id, query)
        elif query.data == "next":
            next_page(app_user, app_user.category_dialogs_count)
            await query_new_allowed_dialogs(user_id, query)
        elif query.data == "back":
            await display_new_allowed_dialogs_options(user_id, query)
        else:
            user_id = update.effective_user.id
            app_user = user_info[user_id]
            await send_updated_inline_keyboard(user_id, query, save_to=app_user.chosen_ids)

    # log_out
    elif app_user.status == UserState.WAIT_FOR_LOG_OUT_CONFIRMATION:
        await query.delete_message()
        if query.data == "Yes":
            await log_out_confirmation(user_id)
        else:
            app_user.status = UserState.WAIT_FOR_SETTINGS_CHOICE

    # settings
    elif app_user.status == UserState.WAIT_FOR_SETTINGS_CHOICE:
        if query.data in ["allow_all", "set_read_after_summary", "preloading"]:
            column = query.data
            flip_user_state(user_id, column)
            await send_updated_inline_keyboard(user_id, query)
        elif query.data == "allowed_dialogs":
            await display_allowed_dialogs(user_id)
        elif query.data == "history_size":
            await display_history_size(user_id)
        elif query.data == "order_by":
            app_user.order_by = "title" if app_user.order_by == "date" else "date"
            update_ordering(user_id, app_user.order_by)
            await settings(user_id=user_id, edit=True)
        elif query.data == "group_info":
            await display_group_info(user_id)
        elif query.data == "log_out":
            await log_out_request(user_id=user_id)
        elif query.data == "back":
            await menu(user_id=user_id, query=query, edit=True)

    # history_size
    elif app_user.status == UserState.WAIT_FOR_HISTORY_SIZE_CHANGE_CONFIRMATION:
        if query.data == "Yes":
            await query_new_history_size(user_id)
        else:
            await settings(user_id=user_id)
        await query.delete_message()


async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    text = update.message.text

    if text is None or text == '':
        return

    if user_id in uninitialized_users:
        uninitialized_user = uninitialized_users[user_id]
        status = uninitialized_user.status
        if status == LoginStage.AWAIT_PHONE_NUMBER:
            await process_phone_number(user_id, text)
        elif status == LoginStage.AWAIT_CODE:
            await process_code(user_id, text)
        elif status == LoginStage.AWAIT_TELEGRAM_PASSWORD:
            await process_password(user_id, text)
        elif status == LoginStage.AWAIT_BOT_PASSWORD:
            await process_bot_password(user_id, text)
        elif status == LoginStage.AWAIT_BOT_PASSWORD_CONFIRMATION:
            await process_bot_password_confirmation(user_id, text)
        elif status == LoginStage.AWAIT_GROUP_NAME:
            await join_group(user_id, text)
        elif status == LoginStage.AWAIT_NEW_GROUP_NAME:
            await query_new_group_password(user_id, text)
        elif status == LoginStage.AWAIT_NEW_GROUP_PASSWORD:
            await query_password_confirmation(user_id, text)
        elif status == LoginStage.AWAIT_NEW_GROUP_PASSWORD_CONFIRMATION:
            await process_password_confirmation(user_id, text)
        elif status == LoginStage.AWAIT_GROUP_PASSWORD:
            await check_group_password(user_id, text)
        return

    if user_id not in user_info:
        return

    status = user_info[user_id].status
    if status == UserState.LOGGED_OUT:
        await check_bot_password(user_id, text)
    if status == UserState.WAIT_FOR_SEARCH_TEXT:
        await process_search_text(user_id, text)
    elif status == UserState.WAIT_FOR_NEW_HISTORY_SIZE:
        await change_history_size(user_id, text)