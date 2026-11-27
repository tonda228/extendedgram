import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from html import unescape
import os
import re
from logging import getLogger
from psycopg import Error as DatabaseError
from telegram.error import TelegramError
from pgvector import Vector
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telethon.tl.types import User, Channel
from telethon.errors import RPCError

from bot import application
from server import select_host
from server.requests import send_request, request_embedding
from utils.classes import UserState
from features.preloading import reset_idle_timer
from database.dialogs import get_allowed_dialogs, update_dialog_priorities, delete_old_dialog_priorities, store_dialog
from database.messages import store_unsaved_messages, get_best_public_messages, get_best_private_messages
from database import connection
from bot.commands.menu import menu
from utils.config import config_file
from utils.helpers import get_message_info, get_full_chat_name, send_message
from utils.state import user_info
from utils.keyboard import disable_inline_keyboard
from utils.check_authentication import check_authentication

PAGE_SIZE = config_file["page_size"]
logger = getLogger(__name__)

async def search_request(update: Update|None=None, context: ContextTypes.DEFAULT_TYPE|None=None, user_id: int|None=None, edit: bool=False, new_request: bool=True) -> None:
    if user_id is None:
        user_id = update.effective_user.id
    if not await check_authentication(user_id):
        return
    app_user = user_info[user_id]
    if app_user.search_in_progress:
        await send_message(user_id, "A search is already running. Please wait for its answer.")
        return
    if new_request:
        app_user.pending_search_query = None
        app_user.search_since = None
        app_user.search_until = None
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
        await menu(user_id=user_id, notice="There are no dialogs or none are allowed.")
        return

    pages_count = max(1, (len(dialogs) + PAGE_SIZE - 1) // PAGE_SIZE)
    app_user.cur_page %= pages_count
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

    await send_message(user_id, f"Choose in which chats you want to search.\nPage {cur_page + 1}/{pages_count}",
                       reply_markup=markup, edit=edit)
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
    user_info[user_id].status = UserState.WAIT_FOR_REQUEST_ANSWER
    await disable_inline_keyboard(user_id)
    for dialog in queried_dialogs:
        store_dialog(dialog, user_id, is_allowed=True)
        update_dialog_priorities(user_id, dialog)
    delete_old_dialog_priorities(user_id)

    user_info[user_id].dialogs = queried_dialogs
    await query_search_text(user_id)


async def query_search_text(user_id: int, edit: bool = False) -> None:
    user = user_info[user_id]
    days = user.history_size or 10
    buttons = [[InlineKeyboardButton("Back", callback_data="back")]]
    if user.pending_search_query:
        buttons = [
            [InlineKeyboardButton(f"Ignore unprocessed images: {'❌' if user.process_images else '✅'}",
                                  callback_data="search_ignore_images")],
            [InlineKeyboardButton("Set date interval", callback_data="search_dates")],
            [InlineKeyboardButton(f"Use history size ({days} days)", callback_data="search_default_dates")],
            *buttons,
        ]
        text = ("Query saved. Choose whether to ignore unprocessed images, then choose the search period to start.\n"
                "This uses the image setting from Settings. Saved descriptions and text captions are still used.")
        user.status = UserState.WAIT_FOR_SEARCH_PERIOD
    else:
        text = "Enter your search query. Next, choose a date interval or use the history size from Settings."
        user.status = UserState.WAIT_FOR_SEARCH_TEXT
    await send_message(user_id, text, reply_markup=InlineKeyboardMarkup(buttons), edit=edit)


async def submit_search_query(user_id: int, text: str) -> None:
    user = user_info[user_id]
    if user.status != UserState.WAIT_FOR_SEARCH_TEXT or not text.strip():
        return
    user.pending_search_query = text.strip()
    await query_search_text(user_id, edit=True)


async def start_pending_search(user_id: int) -> None:
    user = user_info[user_id]
    query = user.pending_search_query
    if not query:
        await query_search_text(user_id, edit=True)
        return
    user.pending_search_query = None
    user.status = UserState.WAIT_FOR_REQUEST_ANSWER
    await disable_inline_keyboard(user_id)
    await process_search_text(user_id, query)


async def query_search_dates(user_id: int) -> None:
    user = user_info[user_id]
    user.status = UserState.WAIT_FOR_SEARCH_DATES
    days = user.history_size or 10
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Ignore unprocessed images: {'❌' if user.process_images else '✅'}",
                              callback_data="search_ignore_images")],
        [InlineKeyboardButton(f"Use history size ({days} days)", callback_data="search_default_dates")],
        [InlineKeyboardButton("Back", callback_data="back")],
    ])
    await send_message(user_id, "Enter the start and end dates: dd.mm.yyyy - dd.mm.yyyy\n"
                       "Example: 01.11.2026 - 25.11.2026. Both dates are included (UTC).",
                       reply_markup=keyboard, edit=True)


async def process_search_dates(user_id: int, text: str) -> None:
    match = re.fullmatch(r"\s*(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})\s*", text)
    try:
        if match is None:
            raise ValueError
        since, end = [datetime.strptime(value, "%d.%m.%Y").replace(tzinfo=timezone.utc) for value in match.groups()]
        if since > end:
            raise ValueError
        until = end + timedelta(days=1)
    except (ValueError, OverflowError):
        await send_message(user_id, "Invalid interval. Use dd.mm.yyyy - dd.mm.yyyy with the start date before or equal to the end date.")
        return
    user_info[user_id].search_since = since
    user_info[user_id].search_until = until
    await start_pending_search(user_id)

async def process_search_text(user_id: int, text: str) -> None:
    app_user = user_info[user_id]
    client = app_user.client
    queried_dialogs = app_user.dialogs
    if client is None or not queried_dialogs or app_user.search_in_progress:
        return

    app_user.search_in_progress = True
    app_user.status = UserState.WAIT_FOR_REQUEST_ANSWER
    message2 = None
    preparation_task = None
    notice = None
    reset_idle_timer(user_id, False)
    try:
        selection = select_host(user_id)
        if selection is None:
            notice = "No host is available with a compatible embedding model."
            return
        _, model = selection
        extra_model = model if model != os.getenv("EMBEDDING_MODEL") else None
        days = app_user.history_size or 10  # Match the legacy zero-day setting in the downloader.
        since = app_user.search_since or datetime.now(timezone.utc) - timedelta(days=days)
        until = app_user.search_until
        label = (f"Preparing search: {since:%d.%m.%Y} – {until - timedelta(days=1):%d.%m.%Y} (UTC)..."
                 if until else f"Preparing search for the last {days} days...")
        message2 = await send_message(user_id, label)
        preparation_stage = "Creating query embedding..."
        checked_messages = 0

        async def animate_preparation() -> None:
            filled_blocks = 0
            while True:
                await asyncio.sleep(3)
                bar = "█" * filled_blocks + "░" * (40 - filled_blocks)
                filled_blocks = (filled_blocks + 4) % 44
                with suppress(TimeoutError, TelegramError):
                    await asyncio.wait_for(send_message(
                        user_id, f"{label}\n{bar}\n{preparation_stage}\nMessages checked: {checked_messages}",
                        edit=True, message=message2), timeout=2)

        if message2 is not None:
            preparation_task = asyncio.create_task(animate_preparation())
        embedding = await request_embedding(user_id, text, model)
        if embedding is None:
            notice = "Could not create an embedding for your query. Check the embedding model on your host."
            return

        best_messages = []
        preparation_failed = False
        for cur_status, dialog in enumerate(queried_dialogs, start=1):
            preparation_stage = f"Chat {cur_status}/{len(queried_dialogs)}"
            checked_messages = 0

            async def report_progress(checked: int) -> None:
                nonlocal checked_messages
                checked_messages = checked

            if not preparation_failed:
                preparation_failed = not await store_unsaved_messages(
                    user_id, dialog, client, True, embedding_model=model,
                    show_download_status=False, since=since, until=until,
                    progress=report_progress)
            if isinstance(dialog[0].entity, Channel):
                best_messages += get_best_public_messages(dialog, embedding, user_id, extra_model, since=since, until=until)
            else:
                best_messages += get_best_private_messages(user_id, dialog, embedding, extra_model, since=since, until=until)

        data = []
        for message in best_messages:
            get_message_info(data, message)

        if not data:
            notice = ("Could not index messages for this search. Check the embedding model on your host."
                       if preparation_failed else "No indexed messages were found in the selected chats.")
            return
        if preparation_failed:
            notice = "Some messages could not be indexed. Searching the messages already saved."
        if preparation_task is not None:
            preparation_task.cancel()
            with suppress(asyncio.CancelledError):
                await preparation_task
            preparation_task = None
        if message2 is not None:
            with suppress(TelegramError):
                await message2.delete()
            message2 = None
        result = await send_request(user_id, data, "search", text, embedding_model=model)
        if not result:
            notice = "Could not process the messages. Check your host and model, then try again."
        elif await send_message(user_id, result, parse_mode="HTML") is None:
            # Invalid or oversized HTML must not hide the model's answer.
            plain_text = unescape(re.sub(r"</?(?:b|i|blockquote)>", "", result))
            await send_message(user_id, plain_text)
    except (DatabaseError, RPCError) as error:
        if isinstance(error, DatabaseError):
            connection.rollback()
        logger.exception("Search failed for user %s", user_id)
        notice = "Could not complete the search. Please try again later."
    finally:
        if preparation_task is not None:
            preparation_task.cancel()
            with suppress(asyncio.CancelledError):
                await preparation_task
        if message2 is not None:
            try:
                await message2.delete()
            except TelegramError:
                pass
        app_user.search_in_progress = False
        if user_info.get(user_id) is app_user:
            if app_user.status == UserState.WAIT_FOR_REQUEST_ANSWER:
                await menu(user_id=user_id, notice=notice)
            else:
                reset_idle_timer(user_id)
