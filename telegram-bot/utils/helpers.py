from datetime import timedelta
from typing import Any
from collections.abc import Awaitable, Callable
from telegram import Message as TelegramMessage
from utils.classes import AppUser

import asyncio
import math
import os

import telegram.error
from telegram import InlineKeyboardMarkup
from telethon.tl.custom import Dialog
from telethon.tl.types import User, Channel, ForumTopic, UpdateDeleteMessages, UpdateDeleteChannelMessages

from bot import application
from utils.classes import DialogDBObject, PublicMessageDBObject, PrivateMessageDBObject
from utils.config import config_file
from utils.state import user_info

async def send_message(chat_id: int, text: str, reply_markup: InlineKeyboardMarkup | None=None, parse_mode: str | None=None, disable_web_page_preview: bool=False, edit: bool=False, message: TelegramMessage|None=None) -> TelegramMessage | None:
    if parse_mode is None and len(text.encode("utf-16-le")) // 2 > 4096:
        chunks = [text[index:index + 2000] for index in range(0, len(text), 2000)]
        result = None
        for index, chunk in enumerate(chunks):
            result = await send_message(
                chat_id, chunk,
                reply_markup=reply_markup if index == len(chunks) - 1 else None,
                disable_web_page_preview=disable_web_page_preview,
                edit=edit and index == 0, message=message if index == 0 else None,
            )
            if result is None:
                return None
        return result
    app_user = user_info.get(chat_id)
    last_message = message if message is not None else (app_user.last_message if app_user else None)
    if edit and last_message is not None:
        sender_func = get_edit_message_text_func(last_message.message_id)
    else:
        sender_func = application.bot.send_message

    retries_left = 5
    while True:
        try:
            new_message = await sender_func(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=disable_web_page_preview)
            if app_user and reply_markup is not None and isinstance(new_message, TelegramMessage):
                app_user.last_message = new_message
            return new_message
        except telegram.error.BadRequest as error:
            if edit and last_message is not None and reply_markup is not None:
                description = str(error).lower()
                if "message is not modified" in description:
                    return last_message
                if "message to edit not found" in description or "message can't be edited" in description:
                    sender_func = application.bot.send_message
                    edit = False
                    continue
            return None
        except telegram.error.RetryAfter as error:
            delay = error.retry_after
            await asyncio.sleep(delay.total_seconds() if isinstance(delay, timedelta) else delay)
        except telegram.error.NetworkError as e:
            retries_left -= 1
            await asyncio.sleep((5 - retries_left))
            if retries_left == 0:
                print("Couldn't send the message")
                return None

def get_db_topic_id(dialog: DialogDBObject) -> int:
    return dialog.topic_id or 0

def get_topic_id(dialog: tuple[Dialog, ForumTopic]) -> int:
    return dialog[1].id if dialog[1] else 0

def get_edit_message_text_func(message_id: int) -> Callable[..., Awaitable[TelegramMessage | bool]]:
    async def func(chat_id: int, text: str, reply_markup: InlineKeyboardMarkup|None=None, parse_mode: str|None=None, disable_web_page_preview: bool=False) -> TelegramMessage | bool:
        return await application.bot.edit_message_text(chat_id=chat_id,
                                                       message_id=message_id,
                                                       text=text, reply_markup=reply_markup,
                                                       parse_mode=parse_mode,
                                                       disable_web_page_preview=disable_web_page_preview)
    return func

def get_user_name(user: User) -> str:
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


def get_full_chat_name(dialog: tuple[Dialog, ForumTopic]) -> str:
    chat_name = f"{dialog[0].title}"
    if dialog[1]:
        chat_name += f"|{dialog[1].title}"
    return chat_name


def get_message_info(data: list[dict], message: PublicMessageDBObject | PrivateMessageDBObject) -> None:
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

def get_request_data(data: list[dict[str, Any]], message_text: str) -> list[dict[str, Any]]:
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

async def resend_processing_status(user_id: int, message: TelegramMessage | None, cur_status: int, msg_count: int, label: str | None = None) -> bool:
    if message is None or msg_count <= 0:
        return False
    percent = min(100, max(0, cur_status * 100 // msg_count))
    blocks = percent * 40 // 100
    download_bar = "█" * blocks + '░' * (40 - blocks) + f" {percent}%"
    if label:
        download_bar = label + "\n" + download_bar
    result = await send_message(user_id, message=message, text=download_bar, edit=True)
    return result is not None

def prev_page(app_user: AppUser, length: int) -> None:
    pages_count = math.ceil(length / config_file["page_size"])
    app_user.cur_page -= 1
    if app_user.cur_page < 0:
        app_user.cur_page = pages_count - 1

def next_page(app_user: AppUser, length: int) -> None:
    pages_count = math.ceil(length / config_file["page_size"])
    app_user.cur_page = (app_user.cur_page + 1) % pages_count

def most_recent(dialog: tuple[Dialog, ForumTopic | None]) -> str:
    return dialog[0].title
