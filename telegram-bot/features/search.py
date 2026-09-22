import os

import telegram
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telethon.tl.types import User, Channel

from utils.classes import UserState
from features.preloading import reset_idle_timer
from llm import llm_client
from database.dialogs import get_allowed_dialogs, update_dialog_priorities, delete_old_dialog_priorities
from database.messages import store_unsaved_messages, get_best_public_messages, get_best_private_messages
from bot.commands.menu import menu
from llm.embeddings import create_embedding
from utils.helpers import get_edit_message_text_func, get_message_info
from utils.state import user_info
from utils.check_authentication import check_authentication

PAGE_SIZE = int(os.environ["PAGE_SIZE"])

async def search_request(update: Update, context: ContextTypes.DEFAULT_TYPE, query=None, edit=False) -> None:
    if not await check_authentication(update, context):
        return

    user_id = update.effective_user.id
    app_user = user_info[user_id]

    reset_idle_timer(user_id)

    dialogs = app_user.dialogs if app_user.dialogs is not None else await get_allowed_dialogs(user_id)

    if len(dialogs) == 0:
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                       text="There are no dialogs or none are allowed.")
        await menu(update, context)
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

    if edit:
        sender_func = get_edit_message_text_func(query.message.message_id, context)
    else:
        sender_func = context.bot.send_message
    try:
        msg = await sender_func(chat_id=update.effective_chat.id,
                                    text="Choose in which chats you want to search.",
                                    reply_markup=markup)
        app_user.status = UserState.WAIT_FOR_SEARCH_CHAT
        user_info[user_id].message_id = msg.id
    except telegram.error.BadRequest as e:
        print(e)

    app_user.dialogs = dialogs

async def process_search_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    reset_idle_timer(user_id)

    given_dialogs = user_info[user_id].dialogs
    reset_idle_timer(user_id)

    queried_dialogs = [given_dialogs[int(index)] for index in user_info[user_id].chosen_ids]
    for dialog in queried_dialogs:
        update_dialog_priorities(user_id, dialog)
    delete_old_dialog_priorities(user_id)

    markup = InlineKeyboardMarkup([[InlineKeyboardButton(text="Back", callback_data="back")]])
    msg = await context.bot.send_message(chat_id=update.effective_chat.id, text="Enter your query bellow:", reply_markup=markup)
    user_info[user_id].message_id = msg.id
    user_info[user_id].status = UserState.WAIT_FOR_SEARCH_TEXT
    user_info[user_id].dialogs = queried_dialogs

async def process_search_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    user_id = update.effective_user.id
    client = user_info[user_id].client
    queried_dialogs = user_info[user_id].dialogs
    embedding = await create_embedding(text)
    best_messages = []

    message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Downloading required messages. It might take a few minutes.")
    reset_idle_timer(user_id, reset=False)

    for dialog in queried_dialogs:
        await store_unsaved_messages(user_id, dialog, client, True)
        if isinstance(dialog[0], Channel):
            best_messages += get_best_public_messages(dialog, embedding)
        else:
            best_messages += get_best_private_messages(user_id, dialog, embedding)

    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=message.message_id,
                                   text="Download is completed.")
    reset_idle_timer(user_id)

    data = []
    for message in best_messages:
        get_message_info(data, message)

    request_data = [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "Give short answer on the following question: " + text
                }
            ],
        },
        {
            "role": "user",
            "content": data
        }
    ]
    response = await llm_client.chat.completions.create(
        model=os.environ["COMPLETIONS_MODEL"],
        messages=request_data)
    result = response.choices[0].message.content
    await context.bot.send_message(chat_id=update.effective_chat.id, text=result, parse_mode="Markdown")

    user_info[user_id].chosen_ids = None

    await menu(update, context)