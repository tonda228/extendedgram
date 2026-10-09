import asyncio
import math
import os

import telegram.error

from bot import application
from utils.state import user_info


async def send_message(chat_id, text, reply_markup=None, parse_mode=None, disable_web_page_preview=False, edit=False):
    app_user = user_info[chat_id]
    if edit and app_user.last_message is not None:
        sender_func = get_edit_message_text_func(app_user.last_message.message_id)
    else:
        sender_func = application.bot.send_message

    retries_left = 5
    while True:
        try:
            new_message = await sender_func(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=disable_web_page_preview)
            app_user.last_message = new_message if app_user else None
            return new_message
        except telegram.error.BadRequest:
            return None
        except telegram.error.NetworkError as e:
            retries_left -= 1
            await asyncio.sleep((5 - retries_left))
            if retries_left == 0:
                print("Couldnt send the message")
                return None

async def accept_user(user_id, bot):
    await send_message(chat_id=user_id, text="Your request have been accepted.")

async def reject_user(user_id, bot):
    await send_message(chat_id=user_id, text="Your request have been rejected. If you think that's a mistake you can resend your request.")

def get_db_topic_id(dialog):
    return dialog.topic_id or 0

def get_topic_id(dialog):
    return dialog[1].id if dialog[1] else 0

def get_edit_message_text_func(message_id):
    async def func(chat_id, text, reply_markup=None, parse_mode=None, disable_web_page_preview=False):
        return await application.bot.edit_message_text(chat_id=chat_id,
                                                       message_id=message_id,
                                                       text=text, reply_markup=reply_markup,
                                                       parse_mode=parse_mode,
                                                       disable_web_page_preview=disable_web_page_preview)
    return func

def get_user_name(user):
    names = []
    if user.username:
        names.append(user.username)
    if user.first_name:
        names.append(user.first_name)
    if user.last_name:
        names.append(user.last_name)
    user_name = " ".join(names)
    if len(names) == 0:
        return "unknown"
    return user_name


def get_full_chat_name(dialog):
    chat_name = f"{dialog[0].title}"
    if dialog[1]:
        chat_name += f"|{dialog[1].title}"
    return chat_name


def get_message_info(data, message):
    if message.user_name:
         name = message.user_name
    else:
        name = message.title

    message_id = str(message.message_id)
    date =  message.date_time.strftime("%H:%M:%S %d.%m.%Y")
    message_text = "From: " + name
    message_text += "Message " + message_id + " " + date + ": " + (message.text if message.text else "")
    media_text = "Media: " + ("None" if message.media_description is None else message.media_description)
    full_text = message_text + "\n" + media_text
    data.append({
        "type": "text",
        "text": full_text
    })

def get_request_data(data, message_text):
    request_data = [
        {
            "role": "system",
            "content":  [
                {
                    "type": "text",
                    "text": message_text
                }
            ],
        },
        {
            "role": "user",
            "content": data
        }
    ]
    return request_data

async def resend_processing_status(user_id, message, cur_status, msg_count):
    processed = int(cur_status / msg_count * 40)
    download_bar = "█" * processed + '░' * (40 - processed) + " " + str(processed) + "%"

    if download_bar == message.text:
        return
    await send_message(user_id, text=download_bar, edit=True)

def prev_page(app_user, length):
    pages_count = math.ceil(length / int(os.environ["PAGE_SIZE"]))
    app_user.cur_page -= 1
    if app_user.cur_page < 0:
        app_user.cur_page = pages_count - 1

def next_page(app_user, length):
    pages_count = math.ceil(length / int(os.environ["PAGE_SIZE"]))
    app_user.cur_page = (app_user.cur_page + 1) % pages_count

def most_recent(dialog):
    return dialog[0].title