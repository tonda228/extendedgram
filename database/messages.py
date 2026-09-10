# check those
import os
import datetime as dt

from pgvector import Vector
from telethon import TelegramClient
from telethon.tl.custom import Message
from telethon.tl.types import User, Channel

from llm.completions import translate_image
from llm.embeddings import create_message_embedding
from state import user_info
from . import cur, connection
from .dialogs import store_dialog
from .users import store_telegram_user


def store_private_message(message: Message, dialog_id: int, user_id: int, sender_id: int, media_description: str | None, embedding: Vector | None):
    cur.execute("""
    INSERT INTO private_message (
        message_id,
        dialog_id,
        user_id,
        sender_id,
        date_time,
        text,
        media_description,
        embedding
    ) VALUES (%s, %s, %s, %s, %s, %s, %s,%s)
        ON CONFLICT DO NOTHING
    """, (message.id, dialog_id, user_id, sender_id, message.date, message.text, media_description, embedding))

def store_public_message(message: Message, dialog_id: int, topic_id: int, sender_id: int, media_description: str | None, embedding: Vector | None):
    cur.execute("""
    INSERT INTO public_message (
        message_id,
        channel_id,
        topic_id,
        sender_id,
        date_time,
        text,
        media_description,
        embedding
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """, (message.id, dialog_id, topic_id, sender_id, message.date, message.text, media_description, embedding))

def get_public_messages(dialog):
    topic_id = dialog[1].id if dialog[1] else 0
    cur.execute("""
                SELECT *
                FROM public_message
                WHERE channel_id = %s
                  AND topic_id = %s
                ORDER BY message_id desc
                """, (dialog[0].id, topic_id))
    return cur.fetchall()

def get_private_messages(dialog, user_id):
    cur.execute("""
               SELECT *
               FROM private_message
               WHERE dialog_id = %s
                 AND user_id = %s
               ORDER BY message_id desc
               """, (dialog[0].id, user_id))
    return cur.fetchall()

def get_best_public_messages(dialog, embedding):
    topic_id = dialog[1].id if dialog[1] else 0

    cur.execute("""
    SELECT pm.*, title, user_name
    FROM public_message pm
    JOIN dialog using (dialog_id, user_id)
    JOIN telegram_user tu
      ON pm.sender_id = tu.user_id
    WHERE channel_id = %s
      and topic_id = %s
    ORDER BY embedding <=> %s
    LIMIT 10
    """, (dialog[0].id, topic_id, embedding))
    return cur.fetchall()

def get_best_private_messages(user_id, dialog, embedding):
    cur.execute("""
    SELECT pm.*, title, user_name
    FROM private_message pm
    JOIN dialog using (dialog_id, user_id)
    JOIN telegram_user tu
      ON pm.sender_id = tu.user_id
    WHERE dialog_id = %s
      and pm.user_id = %s
    ORDER BY embedding <=> %s
    LIMIT 10
    """, (dialog[0].id, user_id, embedding))
    return cur.fetchall()

async def store_unsaved_messages(user_id: int,
                                 dialog,
                                 client: TelegramClient,
                                 add_embeddings = False) -> None:
    # check this one more time

    saved_id = 0
    topic_id = dialog[1].id if dialog[1] else 0
    limit = dialog[1].unread_count if dialog[1] else dialog[0].unread_count
    days = user_info[user_id].history_size
    if days == 0:
        days = 10
    if add_embeddings:
        limit = None
    store_dialog(dialog, user_id)

    # check stored messages here
    if isinstance(dialog[0], Channel):
        saved_messages = get_public_messages(dialog)
    else:
        saved_messages = get_private_messages(dialog, user_id)

    last_id = saved_messages[0].message_id if saved_messages else 0
    if add_embeddings:
        last_id = 0
    if saved_messages is None:
        saved_messages = []

    topic_id_for_search = dialog[1].id if dialog[1] else None

    async for message in client.iter_messages(dialog[0], reply_to=topic_id_for_search, limit=limit):
        if message.id <= last_id or message.date <= dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=days):
            break

        if saved_id < len(saved_messages) and saved_messages[saved_id].message_id == message.id:
            if add_embeddings and saved_messages[saved_id].embedding is None:
                await create_message_embedding(dialog, message, user_id, saved_messages[saved_id].media_description, True)
            saved_id += 1
            continue


        # find way to only download photo without videos and files
        media_description = None
        if message.photo and not message.document:
            if not os.path.exists("media"):
                os.makedirs("media")

            media_description = await translate_image(message)


        embedding = None if not add_embeddings else await create_message_embedding(dialog, message, user_id, media_description, False)
        sender = await message.get_sender()

        sender_id = None
        if isinstance(sender, User):
            if sender.username:
                sender_name = sender.username
            else:
                sender_name = sender.first_name + (sender.last_name if sender.last_name else "")
            store_telegram_user(sender.id, sender_name)
            sender_id = sender.id

        if isinstance(dialog[0], Channel):
            store_public_message(message, dialog[0].id, topic_id, sender_id, media_description, embedding)
        else:
            store_private_message(message, dialog[0].id, user_id, sender_id, media_description, embedding)

        connection.commit()