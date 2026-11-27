from psycopg.rows import class_row

from collections.abc import Awaitable, Callable
import os
import datetime as dt
from time import monotonic
from telegram.error import TelegramError

from pgvector import Vector
from telethon import TelegramClient, utils
from telethon.tl.custom import Message, Dialog
from telethon.tl.types import User, Channel, ForumTopic, MessageActionChatJoinedByLink

from server import select_host
from server.requests import request_media_description, request_message_embedding, request_embeddings
from utils.classes import PublicMessageDBObject, PrivateMessageDBObject
from utils.helpers import get_user_name, resend_processing_status, send_message
from utils.state import user_info
from . import cur, connection
from .dialogs import store_dialog, get_unread_count
from .users import store_telegram_user


async def store_message(message: Message, dialog: tuple[Dialog, ForumTopic | None], user_id: int, add_embeddings: bool=False,
                        embedding_model: str | None = None) -> bool:
    if add_embeddings and embedding_model is None:
        selection = select_host(user_id)
        embedding_model = selection[1] if selection else None
    if embedding_model == os.getenv("EMBEDDING_MODEL"):
        embedding_model = None
    media_description = None
    if message.photo and not message.document and user_info[user_id].process_images:
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
    await store_prepared_message(message, dialog, user_id, media_description, embedding, embedding_model)
    return True


async def store_prepared_message(message: Message, dialog: tuple[Dialog, ForumTopic | None], user_id: int,
                                 media_description: str | None, embedding: Vector | None,
                                 embedding_model: str | None = None) -> None:
    topic_id = dialog[1].id if dialog[1] else 0
    sender = await message.get_sender()

    sender_id = None
    if isinstance(sender, User):
        sender_name = get_user_name(sender)
        store_telegram_user(sender.id, sender_name, commit=False)
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
        store_telegram_user(user.id, sender_name, commit=False)

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
        store_telegram_user(user.id, sender_name, commit=False)

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

def get_saved_message_metadata(dialog: tuple[Dialog, ForumTopic | None], user_id: int,
                               since: dt.datetime) -> dict[int, tuple[str | None, bool]]:
    chat, topic = dialog
    topic_id = topic.id if topic else 0
    with connection.cursor() as db_cur:
        if isinstance(chat.entity, Channel):
            db_cur.execute("""
                SELECT message_id, media_description, embedding IS NOT NULL AS has_embedding
                FROM public_message WHERE channel_id = %s AND topic_id = %s AND date_time >= %s
                """, (chat.entity.id, topic_id, since))
        else:
            db_cur.execute("""
                SELECT message_id, media_description, embedding IS NOT NULL AS has_embedding
                FROM private_message WHERE dialog_id = %s AND user_id = %s AND topic_id = %s AND date_time >= %s
                """, (chat.id, user_id, topic_id, since))
        return {row.message_id: (row.media_description, row.has_embedding) for row in db_cur.fetchall()}


def get_best_public_messages(dialog: tuple[Dialog, ForumTopic | None], embedding: Vector, user_id: int,
                             embedding_model: str | None = None, *, since: dt.datetime | None = None,
                             until: dt.datetime | None = None) -> list[PublicMessageDBObject]:
    since = since or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    until = until or dt.datetime.max.replace(tzinfo=dt.timezone.utc)
    topic_id = dialog[1].id if dialog[1] else 0
    channel_id = dialog[0].entity.id
    if embedding_model:
        best_query = """
            SELECT m.message_id, m.date_time
            FROM public_message_model_embedding e
            JOIN public_message m USING (message_id, channel_id, topic_id)
            WHERE e.model = %s AND e.channel_id = %s AND e.topic_id = %s
              AND m.date_time >= %s AND m.date_time < %s
            ORDER BY e.embedding <=> %s LIMIT 10
        """
        best_params = (embedding_model, channel_id, topic_id, since, until, embedding)
    else:
        best_query = """
            SELECT message_id, date_time FROM public_message
            WHERE channel_id = %s AND topic_id = %s AND embedding IS NOT NULL
              AND date_time >= %s AND date_time < %s
            ORDER BY embedding <=> %s LIMIT 10
        """
        best_params = (channel_id, topic_id, since, until, embedding)

    with connection.cursor(row_factory=class_row(PublicMessageDBObject)) as db_cur:
        db_cur.execute(f"""
        WITH best_message AS (
            {best_query}
        ), nearby AS (
            SELECT message_id FROM best_message
            UNION
            SELECT hit.message_id
            FROM best_message best
            CROSS JOIN LATERAL (
                (SELECT p.message_id FROM public_message p
                 WHERE p.channel_id = %s AND p.topic_id = %s
                   AND p.date_time < %s AND p.date_time >= best.date_time AND p.message_id != best.message_id
                 ORDER BY p.date_time ASC LIMIT 5)
                UNION
                (SELECT p.message_id FROM public_message p
                 WHERE p.channel_id = %s AND p.topic_id = %s
                   AND p.date_time >= %s AND p.date_time < %s AND p.date_time <= best.date_time AND p.message_id != best.message_id
                 ORDER BY p.date_time DESC LIMIT 5)
            ) hit
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
          AND pm1.message_id IN (SELECT message_id FROM nearby)
        ORDER BY pm1.message_id ASC
        """, (*best_params, channel_id, topic_id, until, channel_id, topic_id, since, until, user_id, channel_id, topic_id))
        return db_cur.fetchall()

def get_best_private_messages(user_id: int, dialog: tuple[Dialog, ForumTopic | None], embedding: Vector,
                              embedding_model: str | None = None, *, since: dt.datetime | None = None,
                             until: dt.datetime | None = None) -> list[PrivateMessageDBObject]:
    since = since or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    until = until or dt.datetime.max.replace(tzinfo=dt.timezone.utc)
    dialog_id = dialog[0].id
    topic_id = dialog[1].id if dialog[1] else 0
    if embedding_model:
        best_query = """
            SELECT m.message_id, m.date_time
            FROM private_message_model_embedding e
            JOIN private_message m USING (message_id, dialog_id, user_id, topic_id)
            WHERE e.model = %s AND e.dialog_id = %s AND e.user_id = %s AND e.topic_id = %s
              AND m.date_time >= %s AND m.date_time < %s
            ORDER BY e.embedding <=> %s LIMIT 10
        """
        best_params = (embedding_model, dialog_id, user_id, topic_id, since, until, embedding)
    else:
        best_query = """
            SELECT message_id, date_time FROM private_message
            WHERE dialog_id = %s AND user_id = %s AND topic_id = %s AND embedding IS NOT NULL
              AND date_time >= %s AND date_time < %s
            ORDER BY embedding <=> %s LIMIT 10
        """
        best_params = (dialog_id, user_id, topic_id, since, until, embedding)
    with connection.cursor(row_factory=class_row(PrivateMessageDBObject)) as db_cur:
        db_cur.execute(f"""
        WITH best_message AS (
            {best_query}
        ), nearby AS (
            SELECT message_id FROM best_message
            UNION
            SELECT hit.message_id
            FROM best_message best
            CROSS JOIN LATERAL (
                (SELECT p.message_id FROM private_message p
                 WHERE p.dialog_id = %s AND p.user_id = %s AND p.topic_id = %s
                   AND p.date_time < %s AND p.date_time >= best.date_time AND p.message_id != best.message_id
                 ORDER BY p.date_time ASC LIMIT 5)
                UNION
                (SELECT p.message_id FROM private_message p
                 WHERE p.dialog_id = %s AND p.user_id = %s AND p.topic_id = %s
                   AND p.date_time >= %s AND p.date_time < %s AND p.date_time <= best.date_time AND p.message_id != best.message_id
                 ORDER BY p.date_time DESC LIMIT 5)
            ) hit
        )
        SELECT pm1.*, title, user_name
        FROM private_message pm1
        JOIN dialog using (dialog_id, user_id, topic_id)
        JOIN telegram_user tu
          ON pm1.sender_id = tu.user_id
        WHERE dialog_id = %s
          and pm1.user_id = %s
          and topic_id = %s
          AND pm1.message_id IN (SELECT message_id FROM nearby)
        ORDER BY pm1.message_id ASC
        """, (*best_params, dialog_id, user_id, topic_id, until, dialog_id, user_id, topic_id, since, until, dialog_id, user_id, topic_id))
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
                                 embedding_model: str | None = None,
                                 show_download_status: bool = True,
                                 *, since: dt.datetime | None = None, until: dt.datetime | None = None,
                                 progress: Callable[[int], Awaitable[None]] | None = None) -> bool:
    default_model = os.getenv("EMBEDDING_MODEL")
    if add_embeddings and embedding_model is None:
        selection = select_host(user_id)
        embedding_model = selection[1] if selection else default_model
    if add_embeddings and embedding_model is None:
        return False
    extra_model = embedding_model if add_embeddings and embedding_model != default_model else None
    process_images = user_info[user_id].process_images
    limit = get_unread_count(dialog) if not add_embeddings else None
    if since is None:
        days = user_info[user_id].history_size or 10  # Legacy zero-day setting.
        since = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=days)
    store_dialog(dialog, user_id, update=False)

    saved_messages = get_saved_message_metadata(dialog, user_id, since)
    model_message_ids = get_model_embedding_ids(dialog, user_id, extra_model) if extra_model else set()

    topic_id_for_search = dialog[1].id if dialog[1] else None

    download_message = None
    if show_download_status and msg_count is None:
        download_message = await send_message(chat_id=user_id, text="Downloading required messages. It might take a few minutes.")
    state_message = None
    if msg_count is not None:
        state_message = await send_message(user_id, "Downloading messages...\n" + "░" * 40 + " 0%")

    cur_status = 0
    last_update = monotonic()
    last_percent = 0
    pending: list[tuple[Message, str | None]] = []

    async def flush_embeddings() -> bool:
        if not pending:
            return True
        texts = ["Text: " + (message.text if message.text is not None else "None") +
                 "\nMedia: " + (description if description is not None else "None")
                 for message, description in pending]
        vectors = await request_embeddings(user_id, texts, embedding_model)
        if vectors is None or len(vectors) != len(pending):
            return False

        cached: list[tuple[int, Vector]] = []
        for (message, description), vector in zip(pending, vectors, strict=True):
            if message.id in saved_messages:
                cached.append((message.id, vector))
            else:
                await store_prepared_message(message, dialog, user_id, description, vector, extra_model)
        if cached:
            if extra_model:
                for message_id, vector in cached:
                    store_model_embedding(dialog, user_id, message_id, extra_model, vector)
            else:
                topic_id = dialog[1].id if dialog[1] else 0
                if isinstance(dialog[0].entity, Channel):
                    table = "public_message"
                    condition = "channel_id = %s AND topic_id = %s"
                    scope = (dialog[0].entity.id, topic_id)
                else:
                    table = "private_message"
                    condition = "dialog_id = %s AND user_id = %s AND topic_id = %s"
                    scope = (dialog[0].id, user_id, topic_id)
                cur.executemany(f"UPDATE {table} SET embedding = %s WHERE message_id = %s AND {condition}",
                                [(vector, message_id, *scope) for message_id, vector in cached])
            connection.commit()
        pending.clear()
        return True

    try:
        date_filter = {"offset_date": until} if until is not None else {}
        async for message in client.iter_messages(dialog[0], reply_to=topic_id_for_search, limit=limit, **date_filter):
            if message.date < since:
                break
            if until is not None and message.date >= until:
                continue

            saved = saved_messages.get(message.id)
            media_description = None
            needs_embedding = add_embeddings
            if saved is not None:
                media_description, has_embedding = saved
                if process_images and media_description is None and message.photo and not message.document:
                    description = await request_media_description(user_id, message)
                    if description is not None:
                        update_media_description(dialog, user_id, message.id, description)
                        media_description = description
                        has_embedding = False
                        model_message_ids.discard(message.id)
                needs_embedding = add_embeddings and (message.id not in model_message_ids if extra_model else not has_embedding)
            elif add_embeddings:
                if process_images and message.photo and not message.document:
                    os.makedirs("media", exist_ok=True)
                    media_description = await request_media_description(user_id, message)
            else:
                if not await store_message(message, dialog, user_id, add_embeddings, embedding_model):
                    return False

            if needs_embedding:
                pending.append((message, media_description))

            cur_status += 1
            if progress is not None:
                await progress(cur_status)

            if state_message is not None and msg_count is not None and msg_count > 0:
                percent = min(100, cur_status * 100 // msg_count)
                if percent != last_percent and monotonic() - last_update >= 1:
                    last_update = monotonic()
                    if await resend_processing_status(user_id, state_message, cur_status, msg_count, label="Downloading messages..."):
                        last_update = monotonic()
                        last_percent = percent

        if not await flush_embeddings():
            return False
        return True
    finally:
        for status_message in (state_message, download_message):
            if status_message is not None:
                try:
                    await status_message.delete()
                except TelegramError:
                    pass


def update_media_description(dialog: tuple[Dialog, ForumTopic | None], user_id: int,
                             message_id: int, description: str) -> None:
    topic_id = dialog[1].id if dialog[1] else 0
    is_public = isinstance(dialog[0].entity, Channel)
    try:
        if is_public:
            scope = (message_id, dialog[0].entity.id, topic_id)
            condition = "message_id = %s AND channel_id = %s AND topic_id = %s"
            table = "public_message"
        else:
            scope = (message_id, dialog[0].id, user_id, topic_id)
            condition = "message_id = %s AND dialog_id = %s AND user_id = %s AND topic_id = %s"
            table = "private_message"
        cur.execute(f"UPDATE {table} SET media_description = %s, embedding = NULL WHERE {condition}", (description, *scope))
        cur.execute(f"DELETE FROM {table}_model_embedding WHERE {condition}", scope)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
