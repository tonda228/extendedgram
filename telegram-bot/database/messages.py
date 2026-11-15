from psycopg.rows import class_row

import os
import datetime as dt
from time import monotonic
from telegram.error import TelegramError

from pgvector import Vector
from telethon import TelegramClient, utils
from telethon.tl.custom import Message, Dialog
from telethon.tl.types import User, Channel, ForumTopic, MessageActionChatJoinedByLink

from bot import application
from server import get_host
from server.requests import request_media_description, request_message_embedding
from utils.classes import PublicMessageDBObject, PrivateMessageDBObject
from utils.helpers import get_user_name, resend_processing_status, send_message
from utils.state import user_info
from . import cur, connection
from .dialogs import store_dialog, get_unread_count
from .users import store_telegram_user

async def store_message(message: Message, dialog: tuple[Dialog, ForumTopic | None], user_id: int, add_embeddings: bool=False,
                        embedding_model: str | None = None) -> bool:
    if add_embeddings and embedding_model is None:
        host = await get_host(user_id)
        embedding_model = host.embedding_model if host else None
    if embedding_model == os.getenv("EMBEDDING_MODEL"):
        embedding_model = None
    topic_id = dialog[1].id if dialog[1] else 0
    media_description = None
    if message.photo and not message.document:
        if not os.path.exists("media"):
            os.makedirs("media")

        media_description = await request_media_description(user_id, message)

    embedding = None
    if add_embeddings:
        if embedding_model:
            embedding = await request_message_embedding(dialog, message, user_id, media_description, False, embedding_model)
        else:
            embedding = await request_message_embedding(dialog, message, user_id, media_description, False)
    if add_embeddings and embedding is None:
        return False
    sender = await message.get_sender()

    sender_id = None
    if isinstance(sender, User):
        sender_name = get_user_name(sender)
        store_telegram_user(sender.id, sender_name)
        sender_id = sender.id

    is_channel = isinstance(dialog[0], Channel) or (isinstance(dialog[0], Dialog) and isinstance(dialog[0].entity, Channel))
    if is_channel:
        channel_id = dialog[0].entity.id if isinstance(dialog[0], Dialog) else dialog[0].id
        await store_public_message(message, channel_id, topic_id, user_id, sender_id, media_description,
                                   None if embedding_model else embedding)
    else:
        await store_private_message(message, dialog, user_id, sender_id, media_description,
                                    None if embedding_model else embedding)

    if embedding_model and embedding is not None:
        store_model_embedding(dialog, user_id, message.id, embedding_model, embedding)

    connection.commit()
    return True


def store_model_embedding(dialog: tuple[Dialog | Channel, ForumTopic | None], user_id: int, message_id: int,
                          model: str, embedding: Vector) -> None:
    chat, topic = dialog
    entity = chat.entity if isinstance(chat, Dialog) else chat
    topic_id = topic.id if topic else 0
    if isinstance(entity, Channel):
        cur.execute("""
            INSERT INTO public_message_model_embedding (model, channel_id, topic_id, message_id, embedding)
            VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
            """, (model, entity.id, topic_id, message_id, embedding))
    else:
        cur.execute("""
            INSERT INTO private_message_model_embedding (model, dialog_id, user_id, topic_id, message_id, embedding)
            VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
            """, (model, utils.get_peer_id(entity), user_id, topic_id, message_id, embedding))


def get_model_embedding_ids(dialog: tuple[Dialog, ForumTopic | None], user_id: int, model: str) -> set[int]:
    chat, topic = dialog
    with connection.cursor() as db_cur:
        if isinstance(chat.entity, Channel):
            db_cur.execute("""
                SELECT message_id FROM public_message_model_embedding
                WHERE model = %s AND channel_id = %s AND topic_id = %s
                """, (model, chat.entity.id, topic.id if topic else 0))
        else:
            db_cur.execute("""
                SELECT message_id FROM private_message_model_embedding
                WHERE model = %s AND dialog_id = %s AND user_id = %s AND topic_id = %s
                """, (model, chat.id, user_id, topic.id if topic else 0))
        return {row.message_id for row in db_cur.fetchall()}

def delete_private_message(user_id: int, message_id: int) -> None:
    cur.execute("""
    DELETE
    FROM private_message
    WHERE user_id = %s AND
          message_id = %s
    """, (user_id, message_id))
    connection.commit()

def delete_public_message(channel_id: int, message_id: int) -> None:
    cur.execute("""
    DELETE
    FROM public_message
    WHERE channel_id = %s AND
          message_id = %s
    """, (channel_id, message_id))
    connection.commit()

async def store_private_message(message: Message, dialog: tuple[Dialog, ForumTopic | None], user_id: int, sender_id: int, media_description: str | None, embedding: Vector | None) -> None:
    topic_id = dialog[1].id if dialog[1] else 0
    if isinstance(dialog[0], Dialog):
        dialog = (dialog[0].entity, dialog[1])

    if isinstance(message.action, MessageActionChatJoinedByLink):
        client = user_info[user_id].client
        user = await client.get_entity(message.from_id)

        sender_name = get_user_name(user)
        store_telegram_user(user.id, sender_name)

        text = f"{sender_name} joined via an invite link."
    else:
        text = message.text
    cur.execute("""
    INSERT INTO private_message (
        message_id,
        dialog_id,
        user_id,
        topic_id,
        sender_id,
        date_time,
        text,
        media_description,
        embedding
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """, (message.id, utils.get_peer_id(dialog[0]), user_id, topic_id, sender_id, message.date, text, media_description, embedding))

async def store_public_message(message: Message, channel_id: int, topic_id: int, user_id: int, sender_id: int, media_description: str | None, embedding: Vector | None) -> None:
    if isinstance(message.action, MessageActionChatJoinedByLink):
        client = user_info[user_id].client
        user = await client.get_entity(message.from_id)

        sender_name = get_user_name(user)
        store_telegram_user(user.id, sender_name)

        text = f"{sender_name} joined via an invite link."
    else:
        text = message.text

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
    """, (message.id, channel_id, topic_id, sender_id, message.date, text, media_description, embedding))

def get_public_messages(dialog: tuple[Dialog, ForumTopic | None]) -> list[PublicMessageDBObject]:
    dialog_id = dialog[0].entity.id if isinstance(dialog[0], Dialog) else dialog[0].id
    topic_id = dialog[1].id if dialog[1] else 0
    with connection.cursor(row_factory=class_row(PublicMessageDBObject)) as db_cur:
        db_cur.execute("""
                    SELECT *
                    FROM public_message
                    WHERE channel_id = %s
                      AND topic_id = %s
                    ORDER BY message_id desc
                    """, (dialog_id, topic_id))
        return db_cur.fetchall()

def get_private_messages(dialog: tuple[Dialog, ForumTopic | None], user_id: int) -> list[PrivateMessageDBObject]:
    topic_id = dialog[1].id if dialog[1] else 0
    with connection.cursor(row_factory=class_row(PrivateMessageDBObject)) as db_cur:
        db_cur.execute("""
                   SELECT *
                   FROM private_message
                   WHERE dialog_id = %s
                     AND user_id = %s
                     AND topic_id = %s
                   ORDER BY message_id desc
                   """, (dialog[0].id, user_id, topic_id))
        return db_cur.fetchall()

def get_best_public_messages(dialog: tuple[Dialog, ForumTopic | None], embedding: Vector, user_id: int,
                             embedding_model: str | None = None) -> list[PublicMessageDBObject]:
    topic_id = dialog[1].id if dialog[1] else 0
    channel_id = dialog[0].entity.id
    if embedding_model:
        best_query = """
            SELECT m.message_id, m.date_time
            FROM public_message_model_embedding e
            JOIN public_message m USING (message_id, channel_id, topic_id)
            WHERE e.model = %s AND e.channel_id = %s AND e.topic_id = %s
            ORDER BY e.embedding <=> %s LIMIT 10
        """
        best_params = (embedding_model, channel_id, topic_id, embedding)
    else:
        best_query = """
            SELECT message_id, date_time FROM public_message
            WHERE channel_id = %s AND topic_id = %s AND embedding IS NOT NULL
            ORDER BY embedding <=> %s LIMIT 10
        """
        best_params = (channel_id, topic_id, embedding)

    with connection.cursor(row_factory=class_row(PublicMessageDBObject)) as db_cur:
        db_cur.execute(f"""
        WITH best_message AS (
            {best_query}
        )
        SELECT DISTINCT pm1.*, title, user_name
        FROM public_message pm1
        JOIN channel using (channel_id, topic_id)
        JOIN dialog d
          ON d.channel_id = pm1.channel_id
        AND d.topic_id = pm1.topic_id
        AND d.user_id = %s
        LEFT JOIN telegram_user tu
          ON pm1.sender_id = tu.user_id
        WHERE pm1.channel_id = %s
          and pm1.topic_id = %s
          and EXISTS (
            SELECT 1
            FROM best_message bm1
            WHERE pm1.message_id = bm1.message_id OR
                pm1.message_id in (
                (SELECT message_id
                FROM public_message pm2
                WHERE channel_id = %s
                    AND topic_id = %s
                    AND pm2.date_time >= bm1.date_time
                    AND pm2.message_id != bm1.message_id
                ORDER by pm2.date_time ASC
                LIMIT 5)
                UNION
                (SELECT message_id
                FROM public_message pm2
                WHERE channel_id = %s
                    AND topic_id = %s
                    AND pm2.date_time <= bm1.date_time
                    AND pm2.message_id != bm1.message_id
                ORDER by pm2.date_time DESC
                LIMIT 5)
            )
        )
        ORDER BY pm1.message_id ASC
        """, (*best_params, user_id, channel_id, topic_id, channel_id, topic_id, channel_id, topic_id))
        return db_cur.fetchall()

def get_best_private_messages(user_id: int, dialog: tuple[Dialog, ForumTopic | None], embedding: Vector,
                              embedding_model: str | None = None) -> list[PrivateMessageDBObject]:
    dialog_id = dialog[0].id
    topic_id = dialog[1].id if dialog[1] else 0
    if embedding_model:
        best_query = """
            SELECT m.message_id, m.date_time
            FROM private_message_model_embedding e
            JOIN private_message m USING (message_id, dialog_id, user_id, topic_id)
            WHERE e.model = %s AND e.dialog_id = %s AND e.user_id = %s AND e.topic_id = %s
            ORDER BY e.embedding <=> %s LIMIT 10
        """
        best_params = (embedding_model, dialog_id, user_id, topic_id, embedding)
    else:
        best_query = """
            SELECT message_id, date_time FROM private_message
            WHERE dialog_id = %s AND user_id = %s AND topic_id = %s AND embedding IS NOT NULL
            ORDER BY embedding <=> %s LIMIT 10
        """
        best_params = (dialog_id, user_id, topic_id, embedding)
    with connection.cursor(row_factory=class_row(PrivateMessageDBObject)) as db_cur:
        db_cur.execute(f"""
        WITH best_message AS (
            {best_query}
        )
        SELECT pm1.*, title, user_name
        FROM private_message pm1
        JOIN dialog using (dialog_id, user_id, topic_id)
        JOIN telegram_user tu
          ON pm1.sender_id = tu.user_id
        WHERE dialog_id = %s
          and pm1.user_id = %s
          and topic_id = %s
          and EXISTS (
            SELECT 1
            FROM best_message bm1
            WHERE pm1.message_id = bm1.message_id OR
                pm1.message_id in (
                (SELECT message_id
                FROM private_message pm2
                WHERE dialog_id = %s
                    AND pm2.user_id = %s
                    AND pm2.topic_id = %s
                    AND pm2.date_time >= bm1.date_time
                    AND pm2.message_id != bm1.message_id
                ORDER by pm2.date_time ASC
                LIMIT 5)
                UNION
                (SELECT message_id
                FROM private_message pm2
                WHERE dialog_id = %s
                    AND pm2.user_id = %s
                    AND pm2.topic_id = %s
                    AND pm2.date_time <= bm1.date_time
                    AND pm2.message_id != bm1.message_id
                ORDER by pm2.date_time DESC
                LIMIT 5)
            )
        )
        ORDER BY pm1.message_id ASC
        """, (*best_params, dialog_id, user_id, topic_id, dialog_id, user_id, topic_id, dialog_id, user_id, topic_id))
        return db_cur.fetchall()

# come up with better name
def get_public_messages_for_summarization(channel: tuple[Dialog, ForumTopic | None], messages_count: int, user_id: int) -> list[PublicMessageDBObject]:
    topic_id = channel[1].id if channel[1] else 0

    with connection.cursor(row_factory=class_row(PublicMessageDBObject)) as db_cur:
        db_cur.execute("""
        select distinct *
        from (select pm.*, user_name, title
            from public_message pm
            join dialog d
                on d.channel_id = pm.channel_id
                and d.topic_id = pm.topic_id
                and d.user_id = %s
            left join telegram_user tu
                on pm.sender_id = tu.user_id
            where pm.channel_id = %s
               and pm.topic_id = %s
            order by message_id desc
            limit %s)
        order by message_id asc
        """, (user_id, channel[0].entity.id, topic_id, messages_count))

        return db_cur.fetchall()

def get_private_messages_for_summarization(dialog: tuple[Dialog, None], user_id: int, messages_count: int) -> list[PrivateMessageDBObject]:
    with connection.cursor(row_factory=class_row(PrivateMessageDBObject)) as db_cur:
        db_cur.execute("""
        select distinct *
        from (select pm.*, user_name, title
              from private_message pm
              join dialog d using (dialog_id, user_id, topic_id)
              join telegram_user tu
                  on pm.sender_id = tu.user_id
              where pm.dialog_id = %s
                and pm.user_id = %s
              order by message_id desc
              limit %s)
        order by message_id asc
        """, (dialog[0].id, user_id, messages_count))
        return db_cur.fetchall()

async def store_unsaved_messages(user_id: int,
                                 dialog: tuple[Dialog, ForumTopic | None],
                                 client: TelegramClient,
                                 add_embeddings: bool = False,
                                 msg_count: int|None = None,
                                 embedding_model: str | None = None) -> bool:
    default_model = os.getenv("EMBEDDING_MODEL")
    if add_embeddings and embedding_model is None:
        host = await get_host(user_id)
        embedding_model = host.embedding_model if host else default_model
    extra_model = embedding_model if add_embeddings and embedding_model != default_model else None
    saved_id = 0
    limit = get_unread_count(dialog) if not add_embeddings else None
    days = user_info[user_id].history_size
    # if history_size was set to 0, only check the last 10 days to prevent blank result
    if days == 0:
        days = 10
    store_dialog(dialog, user_id, update=False)

    if isinstance(dialog[0].entity, Channel):
        saved_messages = get_public_messages(dialog)
    else:
        saved_messages = get_private_messages(dialog, user_id)
    model_message_ids = get_model_embedding_ids(dialog, user_id, extra_model) if extra_model else set()

    topic_id_for_search = dialog[1].id if dialog[1] else None

    download_message = await send_message(chat_id=user_id, text="Downloading required messages. It might take a few minutes.")
    state_message = None
    if msg_count is not None:
        state_message = await send_message(user_id, "░" * 40 + " 0%")

    cur_status = 0
    last_update = monotonic()
    last_percent = 0
    try:
        async for message in client.iter_messages(dialog[0], reply_to=topic_id_for_search, limit=limit):
            if message.date <= dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=days):
                break

            while saved_id < len(saved_messages) and saved_messages[saved_id].message_id > message.id:
                saved_id += 1

            if saved_id < len(saved_messages) and saved_messages[saved_id].message_id == message.id:
                if extra_model and message.id not in model_message_ids:
                    result = await request_message_embedding(dialog, message, user_id,
                                                             saved_messages[saved_id].media_description, False, extra_model)
                    if result is None:
                        return False
                    store_model_embedding(dialog, user_id, message.id, extra_model, result)
                    connection.commit()
                elif add_embeddings and not extra_model and saved_messages[saved_id].embedding is None:
                    result = await request_message_embedding(dialog, message, user_id, saved_messages[saved_id].media_description, True)
                    if result is None:
                        return False
                saved_id += 1
            elif not await store_message(message, dialog, user_id, add_embeddings, embedding_model):
                return False

            if state_message is not None and msg_count is not None and msg_count > 0:
                cur_status += 1
                percent = min(100, cur_status * 100 // msg_count)
                if percent != last_percent and monotonic() - last_update >= 1:
                    last_update = monotonic()
                    if await resend_processing_status(user_id, state_message, cur_status, msg_count):
                        last_update = monotonic()
                        last_percent = percent

        if download_message is not None:
            await send_message(user_id, "Download is completed.", edit=True, message=download_message)
        return True
    finally:
        if state_message is not None:
            try:
                await state_message.delete()
            except TelegramError:
                pass
