import html

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telethon import functions
from telethon.tl.types import User, Channel

from bot.commands.menu import menu
from llm.completions import send_data
from utils.classes import UserState
from database.dialogs import get_allowed_dialogs, get_unread_count
from database.messages import store_unsaved_messages, get_public_messages_for_summarization, \
    get_private_messages_for_summarization
from features.preloading import reset_idle_timer
from utils.config import config_file
from utils.state import user_info
from utils.check_authentication import check_authentication
from utils.helpers import get_message_info, send_message

PAGE_SIZE = config_file["page_size"]

async def summarize_request(update: Update|None=None, context: ContextTypes.DEFAULT_TYPE|None=None, user_id=None, query=None, edit=False):
    if user_id is None:
        user_id = update.effective_user.id
    if not await check_authentication(user_id):
        return
    app_user = user_info[user_id]
    reset_idle_timer(user_id)

    if app_user.dialogs is None:
        app_user.allowed_dialogs = None
        allowed_dialogs = await get_allowed_dialogs(user_id)
        if len(allowed_dialogs) == 0:
            await send_message(user_id, text="No dialog is allowed. You can change it in settings")
            await menu(user_id=user_id)
            return
        unread_dialogs = [dialog for dialog in allowed_dialogs if get_unread_count(dialog) > 0]
        unread_dialogs.sort(key=lambda dialog: get_unread_count(dialog), reverse=True)
    else:
        unread_dialogs = app_user.dialogs

    cur_page = app_user.cur_page
    start = cur_page * PAGE_SIZE
    end = start + PAGE_SIZE

    keyboard = []

    for index, dialog in enumerate(unread_dialogs[start:end], start=start):
        unread_count = get_unread_count(dialog)
        chat_name = f"{dialog[0].title}"
        if dialog[1]:
            chat_name += f"|{dialog[1].title}"

        dialog_text = f"{chat_name}: {unread_count} unread message{"" if unread_count == 1 else "s"}"
        keyboard.append([InlineKeyboardButton(text=dialog_text, callback_data=str(index))])

    if len(unread_dialogs) == 0:
        await send_message(user_id, text="All dialogs are read. Come back later.")
        await menu(user_id=user_id)
        return

    if len(unread_dialogs) > PAGE_SIZE:
        keyboard += [[InlineKeyboardButton(text="◀ Prev", callback_data="prev"),
                      InlineKeyboardButton(text="Next ▶️", callback_data="next")]]
    keyboard.append([InlineKeyboardButton(text="Back", callback_data="back")])
    markup = InlineKeyboardMarkup(keyboard)

    await send_message(user_id, "Choose which chat you want to summarize.", reply_markup=markup, edit=edit)
    app_user.status = UserState.WAIT_FOR_SUMMARIZE_CHAT
    app_user.dialogs = unread_dialogs

async def process_summarize_query(user_id, query):
    app_user = user_info[user_id]
    client = app_user.client
    unread_dialogs = app_user.dialogs
    dialog_id = query.data
    try:
        dialog_id = int(dialog_id)
    except ValueError:
        await send_message(user_id, text="Invalid argument. Try again.")
        return
    if dialog_id < 0 or dialog_id >= len(unread_dialogs):
        await send_message(user_id, text="Your choice is out of range. Try again.")
        return

    chosen_dialog = unread_dialogs[dialog_id]
    data = []
    messages_count = chosen_dialog[0].unread_count if not chosen_dialog[1] else chosen_dialog[1].unread_count

    message1 = await context.bot.send_message(chat_id=update.effective_chat.id,
                                             text="Downloading required messages. It might take a few minutes.")
    message2 = await context.bot.send_message(user_id, "|" + " " * 100 + "| 0%")
    reset_idle_timer(user_id, reset=False)
    await store_unsaved_messages(user_id, chosen_dialog, client, False, context.bot,0, messages_count, message2)
    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=message1.message_id,
                                        text="Download is completed.")
    await context.bot.delete_message(user_id, message2.id)
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
    format_text = f"\nResponse give in json in following format:\n {response_format}"
    message_text = ("Summarize the messages very concisely. "
                    "For each message, you first receive the sender name and then the message. "
                    "Group related messages into topics, but do not merge unrelated conversations. "
                    "For each topic, include the first and last message IDs. "
                    "Return at most 10 topics. "
                    "Prioritize only important information, decisions, questions, plans, and conclusions. "
                    "Ignore greetings, repetition, jokes, filler, and minor details unless they are necessary to understand the topic. "
                    "For every 20 input messages, produce approximately 1 topic summary when possible. "
                    "Each topic summary should normally be 1-5 sentences and no more than 150 words. "
                    "Use a surface-level summary only: do not retell the conversation message by message. "
                    "Do not include details that are not essential. "
                    "If several messages repeat the same idea, mention it only once. "
                    "Respond in the same language as the messages you received "
                    "If the conversation is short or contains little important information, return fewer topics rather than adding detail. "
                    "Add information about who says what if that person talks about his situation" + format_text)

    message = await context.bot.send_message(user_id, "|" + " " * 100 + "| 0%")
    try:
        results = await send_data(update, context, data, message_text, message, True, 5, 0, len(data))
    except json.decoder.JSONDecodeError:
        return

    if len(results) > 10:
        message_text = "Combine those topics. Leave only 10 topics." + format_text
        data = [
            {"type": "text", "text": json.dumps(result)}
            for result in results
        ]
        results = await send_data(update, context, data, message_text, for_summary=True)

    await context.bot.delete_message(user_id, message.id)

    for index, result in enumerate(results, start=1):
        messages = ""
        if isinstance(chosen_dialog[0].entity, Channel):
            url_start = await client(functions.channels.ExportMessageLinkRequest(
                channel=chosen_dialog[0].entity,
                id=result["start_message_id"]
            ))
            url_end = await client(functions.channels.ExportMessageLinkRequest(
                channel=chosen_dialog[0].entity,
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
        if chosen_dialog[1] is None:
            await app_user.client.send_read_acknowledge(chosen_dialog[0], clear_mentions=True, clear_reactions=True)
        else:
            await app_user.client(
                functions.messages.ReadDiscussionRequest(
                    peer=chosen_dialog[0].entity,
                    msg_id=chosen_dialog[1].id,
                    read_max_id=chosen_dialog[1].top_message
                )
            )
        await menu(user_id=user_id)
        return

    keyboard = [
        [
            InlineKeyboardButton(text="Yes", callback_data="Yes"),
            InlineKeyboardButton(text="No", callback_data="No")
        ]
    ]
    await send_message(user_id, "Mark chat as read?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    app_user.status = UserState.WAIT_FOR_READ
    app_user.last_read = chosen_dialog