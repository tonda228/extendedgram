
import datetime as dt

from psycopg.rows import class_row
from telethon.tl.custom import Dialog
from telethon.tl.types import User, Channel, ForumTopic
from telethon import functions, utils

from utils.classes import DialogDBObject, DialogPriorityDBObject
from utils.helpers import get_topic_id
from utils.state import user_info
from . import cur, connection

def store_channel(channel: tuple[Dialog | Channel, ForumTopic | None]) -> None:
    topic_id = channel[1].id if channel[1] else 0
    topic_name = channel[1].title if channel[1] else None

    cur.execute("""
    INSERT INTO channel (channel_id, topic_id, topic_name)
    VALUES (%s, %s, %s) ON CONFLICT (channel_id, topic_id) DO UPDATE
        SET topic_name = EXCLUDED.topic_name
    """, (channel[0].id, topic_id, topic_name))

def store_dialog(dialog: tuple[Dialog, ForumTopic], user_id: int, is_allowed: bool=False, update: bool=True) -> None:
    if isinstance(dialog[0], Dialog):
        dialog = (dialog[0].entity, dialog[1])

    is_channel = isinstance(dialog[0], Channel)

    channel_id = dialog[0].id if is_channel else None
    topic_id = dialog[1].id if dialog[1] else 0

    if is_channel:
        store_channel(dialog)

    is_user = isinstance(dialog[0], User)
    if is_user:
        if dialog[0].username:
            dialog_title = dialog[0].username
        else:
            first_name = (dialog[0].first_name if dialog[0].first_name else "")
            dialog_title = first_name + (dialog[0].last_name if dialog[0].last_name else "")
    else:
        dialog_title = dialog[0].title

    is_allowed = user_info[user_id].allow_all or is_allowed
    query = """
    INSERT INTO dialog (dialog_id, user_id, title, is_allowed, channel_id, topic_id)
    VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (dialog_id, user_id, topic_id) DO """
    if not update:
        query += "NOTHING"
    else:
        query += """UPDATE
        SET title = EXCLUDED.title,
            is_allowed = EXCLUDED.is_allowed"""
    cur.execute(query, (utils.get_peer_id(dialog[0]), user_id, dialog_title, is_allowed, channel_id, topic_id))
    connection.commit()

def delete_unused_channels(commit: bool = True) -> None:
    cur.execute("""
    --delete channels that don't have dialogs connected to them
    DELETE
    FROM channel as ch
    WHERE NOT EXISTS (
        SELECT 1
        FROM dialog d
        WHERE d.channel_id = ch.channel_id
    )""")
    if commit:
        connection.commit()

def delete_dialog(dialog_id: int, user_id: int) -> None:
    cur.execute("""
    DELETE
    FROM dialog
    WHERE dialog_id = %s AND
          user_id = %s
    """, (dialog_id, user_id))
    connection.commit()

def sync_dialogs(dialogs: list[tuple[Dialog, ForumTopic]], user_id: int) -> None:
    current_dialogs_set = {dialog[0].id for dialog in dialogs}
    with connection.cursor(row_factory=class_row(DialogDBObject)) as db_cur:
        db_cur.execute("""
        SELECT *
        FROM dialog
        WHERE user_id = %s
        """, (user_id, ))
        saved_dialogs = db_cur.fetchall()

    for dialog in saved_dialogs:
        if dialog.dialog_id not in current_dialogs_set:
            delete_dialog(dialog.dialog_id, user_id)
    delete_unused_channels()

# cant handle a lot of dialogs due to timeout
async def get_all_dialogs(user_id: int, topics: bool=True) -> list[tuple[Dialog, ForumTopic | None]]:
    app_user = user_info[user_id]
    dialogs = []

    client = app_user.client
    if client is None:
        return dialogs

    async for dialog in client.iter_dialogs():
        if topics and dialog.is_group and getattr(dialog.entity, "forum", False):
            offset_date = None
            offset_id = 0
            offset_topic = 0
            seen_topics: set[int] = set()
            while True:
                result = await client(functions.messages.GetForumTopicsRequest(
                    peer=dialog, offset_date=offset_date, offset_id=offset_id,
                    offset_topic=offset_topic, limit=100,
                ))
                page = [topic for topic in result.topics if isinstance(topic, ForumTopic)]
                new_topics = [topic for topic in page if topic.id not in seen_topics]
                if not new_topics:
                    break
                for topic in new_topics:
                    seen_topics.add(topic.id)
                    dialogs.append((dialog, topic))
                if len(seen_topics) >= result.count:
                    break
                last_topic = page[-1]
                offset_topic = last_topic.id
                offset_id = last_topic.top_message
                if result.order_by_create_date:
                    offset_date = last_topic.date
                else:
                    top_message = next((message for message in result.messages if message.id == offset_id), None)
                    offset_date = top_message.date if top_message is not None else last_topic.date
            continue
        dialogs.append((dialog, None))
    sync_dialogs(dialogs, user_id)
    return dialogs

def update_dialog_priorities(user_id: int, dialog: tuple[Dialog, ForumTopic | None]) -> None:
    topic_id = dialog[1].id if dialog[1] else 0
    cur.execute("""
    INSERT INTO dialog_priority (
        dialog_id,
        user_id,
        topic_id,
        date_time)
    VALUES (%s, %s, %s, %s)
    """, (dialog[0].id, user_id, topic_id, dt.datetime.now()))

def delete_old_dialog_priorities(user_id: int) -> None:
    cur.execute("""
    DELETE FROM dialog_priority
    WHERE user_id = %s
      AND (dialog_id, topic_id, date_time) NOT IN (
          SELECT dialog_id, topic_id, date_time
          FROM dialog_priority
          WHERE user_id = %s
          ORDER BY date_time DESC, dialog_id DESC, topic_id DESC
          LIMIT 100
      )
    """, (user_id, user_id))
    connection.commit()

async def get_allowed_dialogs(user_id: int) -> list[tuple[Dialog, ForumTopic | None]]:
    app_user = user_info[user_id]
    if not app_user.allow_all and app_user.allowed_dialogs is not None:
        return app_user.allowed_dialogs

    all_dialogs = await get_all_dialogs(user_id)
    if app_user.allow_all:
        app_user.allowed_dialogs_set = set()
        return all_dialogs

    with connection.cursor(row_factory=class_row(DialogDBObject)) as db_cur:
        db_cur.execute("""
        SELECT *
        FROM dialog
        WHERE user_id = %s
          AND is_allowed = TRUE
        """, (user_id,))
        allowed_dialogs_set = {
            (dialog.dialog_id, dialog.topic_id)
            for dialog in
            db_cur.fetchall()
        }

    allowed_dialogs = []
    for dialog in all_dialogs:
        if (dialog[0].id, get_topic_id(dialog)) not in allowed_dialogs_set:
            continue
        allowed_dialogs.append(dialog)
    app_user.allowed_dialogs = allowed_dialogs
    app_user.allowed_dialogs_set = allowed_dialogs_set
    return allowed_dialogs

def get_unread_count(dialog: tuple[Dialog, ForumTopic]) -> int:
    return dialog[1].unread_count if dialog[1] else dialog[0].unread_count

async def get_recent_dialogs(user_id: int) -> list[tuple[Dialog, ForumTopic | None]]:
    allowed_dialogs = await get_allowed_dialogs(user_id)

    with connection.cursor(row_factory=class_row(DialogPriorityDBObject)) as db_cur:
        db_cur.execute("""
        SELECT dialog_id, count(*) as count
        FROM (SELECT *
              FROM dialog_priority
              WHERE user_id = %s
              ORDER BY date_time desc
              LIMIT 100)
        GROUP BY dialog_id
        ORDER BY count(*)
        """, (user_id,))
        priorities = db_cur.fetchall()
    dialog_to_index = {
        dialog.dialog_id: index
        for index, dialog in enumerate(priorities)
    }
    sorted_dialogs = sorted(
        (dialog for dialog in allowed_dialogs if dialog[0].id in dialog_to_index),
        key=lambda dialog: dialog_to_index[dialog[0].id],
    )
    unused_dialogs = [
        dialog for dialog in allowed_dialogs if dialog[0].id not in dialog_to_index
    ]
    unused_dialogs.sort(reverse=True, key=get_unread_count)
    return sorted_dialogs + unused_dialogs
