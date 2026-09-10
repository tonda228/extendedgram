from pgvector import Vector
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telethon.tl.types import User, Channel

from classes import UserState
from llm import requests_client, URL
from database.dialogs import get_allowed_dialogs, update_dialog_priorities, delete_old_dialog_priorities
from database.messages import store_unsaved_messages, get_best_public_messages, get_best_private_messages
from bot.commands.menu import menu
from llm.embeddings import create_embedding
from state import user_info
from utils import check_authentication, reset_idle_timer, get_message_info


async def search_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_authentication(update, context):
        return

    user_id = update.effective_user.id
    app_user = user_info[user_id]

    reset_idle_timer(user_id)

    dialogs = await get_allowed_dialogs(user_id)

    text = "Choose in which chats you want to search. Separate chats indexes by coma. All whitespaces will be ignored."
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    dialogs_text = ""

    for index, dialog in enumerate(dialogs, start=1):
        if index != 1:
            dialogs_text += '\n'
        dialog_name = dialog[0].title
        if dialog[1]:
            dialog_name += f"|{dialog[1].title}"
        dialogs_text += f"{index}) {dialog_name}"


    back_button = InlineKeyboardMarkup([[InlineKeyboardButton(text="Back", callback_data="back")]])
    await context.bot.send_message(chat_id=update.effective_chat.id, text=dialogs_text, reply_markup=back_button)
    app_user.status = UserState.WAIT_FOR_SEARCH_CHAT
    app_user.dialogs = dialogs

async def process_search_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, text) -> None:
    user_id = update.effective_user.id
    reset_idle_timer(user_id)

    given_dialogs = user_info[user_id].dialogs
    reset_idle_timer(user_id)

    try:
        indexes = [int(index) for index in text.split(",")]
    except ValueError:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid input. Try again:")
        return
    if 0 in indexes:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Thank you for your time.")
        user_info[user_id].status = UserState.AUTHENTICATED
        return
    for index in indexes:
        if not (1 <= index <= len(given_dialogs)):
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid index. Try again:")
            return

    queried_dialogs = [given_dialogs[index - 1] for index in indexes]
    for dialog in queried_dialogs:
        update_dialog_priorities(user_id, dialog)
    delete_old_dialog_priorities(user_id)

    user_info[user_id].status = UserState.WAIT_FOR_SEARCH_TEXT
    user_info[user_id].dialogs = queried_dialogs

    await context.bot.send_message(chat_id=update.effective_chat.id, text="Enter your query bellow:")

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
        await get_message_info(data, message)

    request_data = {
        "model": "docker.io/ai/qwen3-vl:8B",
        "messages": [
            {
                "role": "system",
                "content": "Give short answer on the following question: " + text
            },
            {
                "role": "user",
                "content": data
            }
        ]
    }
    response = await requests_client.post(URL, json=request_data)
    result = response.json()["choices"][0]["message"]["content"]
    await context.bot.send_message(chat_id=update.effective_chat.id, text=result, parse_mode="Markdown")

    await menu(update, context)