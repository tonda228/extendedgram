import html
import json
import os

import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telethon import functions
from telethon.tl.types import User, Channel

from bot.commands.menu import menu
from utils.classes import UserState
from database.dialogs import get_allowed_dialogs, get_unread_count
from database.messages import store_unsaved_messages, get_public_messages_for_summarization, \
    get_private_messages_for_summarization
from features.preloading import reset_idle_timer
from utils.state import user_info
from llm import llm_client
from utils.check_authentication import check_authentication
from utils.helpers import get_message_info


async def summarize_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_authentication(update, context):
        return
    user_id = update.effective_user.id

    reset_idle_timer(user_id)

    allowed_dialogs = await get_allowed_dialogs(user_id)
    if len(allowed_dialogs) == 0:
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                       text="No dialog is allowed. You can change it in settings")
        await menu(update, context)
        return

    keyboard = []
    unread_dialogs = [dialog for dialog in allowed_dialogs if get_unread_count(dialog) > 0]
    for index, dialog in enumerate(unread_dialogs):
        unread_count = get_unread_count(dialog)
        chat_name = f"{dialog[0].title}"
        if dialog[1]:
            chat_name += f"|{dialog[1].title}"

        dialog_text = f"{chat_name}: {unread_count} unread message{"" if unread_count == 1 else "s"}"
        keyboard.append([InlineKeyboardButton(text=dialog_text, callback_data=str(index))])

    if len(unread_dialogs) == 0:
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                       text="All dialogs are read. Come back later.")
        await menu(update, context)
        return

    keyboard.append([InlineKeyboardButton(text="Back", callback_data="back")])
    markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Choose which chat you want to summarize.",
        reply_markup=markup
    )

    user_info[user_id].status = UserState.WAIT_FOR_SUMMARIZE_CHAT
    user_info[user_id].dialogs = unread_dialogs

async def process_summarize_query(update: Update, context: ContextTypes.DEFAULT_TYPE, query):
    if not update.effective_user:
        return

    text = query.data
    user_id = update.effective_user.id
    app_user = user_info[user_id]
    client = user_info[user_id].client
    unread_dialogs = user_info[user_id].dialogs
    dialog_id = text.split()[0]
    try:
        dialog_id = int(dialog_id)
    except ValueError:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid argument. Try again.")
        return
    if dialog_id < 0 or dialog_id >= len(unread_dialogs):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Your choice is out of range. Try again.")
        return

    chosen_dialog = unread_dialogs[dialog_id]
    data = []
    messages_count = chosen_dialog[0].unread_count if not chosen_dialog[1] else chosen_dialog[1].unread_count

    message = await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text="Downloading required messages. It might take a few minutes.")
    reset_idle_timer(user_id, reset=False)

    await store_unsaved_messages(user_id, chosen_dialog, client, False)

    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=message.message_id,
                                        text="Download is completed.")

    reset_idle_timer(user_id)

    if isinstance(chosen_dialog[0].entity, Channel):
        messages = get_public_messages_for_summarization(chosen_dialog, messages_count)
    else:
        messages = get_private_messages_for_summarization(chosen_dialog, user_id, messages_count)

    for message in messages:
        get_message_info(data, message)

    response_format = """
    [
        {
            "start_message_id": 123,
            "end_message_id": 456,
            "topic": "...",
            "summary": "..."
        }
    ]
    """
    request_data = [
        {
            "role": "system",
            "content":  [
                {
                    "type": "text",
                    "text": "Summarize the messages very concisely. "
                            "For each message, you first receive the sender name and then the message. "
                            "Group related messages into topics, but do not merge unrelated conversations. "
                            "For each topic, include the first and last message IDs. "
                            "Return at most 20 topics. "
                            "Prioritize only important information, decisions, questions, plans, and conclusions. "
                            "Ignore greetings, repetition, jokes, filler, and minor details unless they are necessary to understand the topic. "
                            "For every 20 input messages, produce approximately 1 topic summary when possible. "
                            "Each topic summary should normally be 1-3 sentences and no more than 60 words. "
                            "Use a surface-level summary only: do not retell the conversation message by message. "
                            "Do not include examples, background explanations, or details that are not essential. "
                            "If several messages repeat the same idea, mention it only once. "
                            "If the conversation is short or contains little important information, return fewer topics rather than adding detail. "
                            "Add information about who says what if that person talks about his situation "
                            f"Response give in json in following format:\n {response_format}"
                }
            ],
        },
        {
            "role": "user",
            "content": data
        }
    ]
    while True:
        try:
            response = await llm_client.chat.completions.create(
                model=os.environ["COMPLETIONS_MODEL"],
                messages=request_data)
            results = json.loads(response.choices[0].message.content)
            # results = json.loads(response.json()["choices"][0]["message"]["content"])
        except json.decoder.JSONDecodeError:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Error occurred. Retrying...")
        else:
            break

    for index, result in enumerate(results, start=1):
        messages = ""
        if isinstance(chosen_dialog[0], Channel):
            url_start = await client(functions.channels.ExportMessageLinkRequest(
                channel=chosen_dialog[0],
                id=result["start_message_id"]
            ))
            url_end = await client(functions.channels.ExportMessageLinkRequest(
                channel=chosen_dialog[0],
                id=result["end_message_id"]
            ))
            link_start = f"<a href='{url_start.link}'>here</a>"
            link_end = f"<a href='{url_end.link}'>here</a>"
            messages = f"From {link_start} to {link_end}\n"
        topic = html.escape(str(result["topic"]))
        summary = html.escape(str(result["summary"]))
        await query.message.reply_html(text=f"{index}) {topic}\n"
                                             f"{messages}{summary}",
                                        disable_web_page_preview=True)


    if app_user.set_read_after_summary:
        await app_user.client.send_read_acknowledge(chosen_dialog[0], clear_mentions=True, clear_reactions=True)
        await menu(update, context)
        return
    keyboard = [
        [
            InlineKeyboardButton(text="Yes", callback_data="Yes"),
            InlineKeyboardButton(text="No", callback_data="No")
        ]
    ]

    await query.message.reply_text(text="Mark chat as read?", reply_markup=InlineKeyboardMarkup(keyboard))
    user_info[user_id].status = UserState.WAIT_FOR_READ
    user_info[user_id].last_read = chosen_dialog