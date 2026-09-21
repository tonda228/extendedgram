import datetime as dt

from telethon.tl.custom import Dialog
from telethon.tl.types import User, Channel, ForumTopic
from telethon import functions, utils

from utils.helpers import get_db_topic_id, get_topic_id
from utils.state import user_info
from . import cur, connection

def store_channel(channel):
    topic_id = channel[1].id if channel[1] else 0
    topic_name = channel[1].title if channel[1] else None

    cur.execute("""
    INSERT INTO channel (channel_id, topic_id, topic_name)
    VALUES (%s, %s, %s) ON CONFLICT (channel_id, topic_id) DO UPDATE
        SET topic_name = EXCLUDED.topic_name
    """, (channel[0].id, topic_id, topic_name))

def store_dialog(dialog, user_id, is_allowed=False, update=True):
    if isinstance(dialog[0], Dialog):
        dialog = (dialog[0].entity, dialog[1])

    is_channel = isinstance(dialog[0], Channel)

    channel_id = dialog[0].id if is_channel else None
    topic_id = dialog[1].id if dialog[1] else None

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
    VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (dialog_id, user_id) DO """
    if not update:
        query += "NOTHING"
    else:
        query += """UPDATE
        SET title = EXCLUDED.title,
            is_allowed = EXCLUDED.is_allowed"""

    cur.execute(query, (utils.get_peer_id(dialog[0]), user_id, dialog_title, is_allowed, channel_id, topic_id))
    connection.commit()

def delete_unused_channels():
    cur.execute("""
    --delete channels that don't have dialogs connected to them
    DELETE
    FROM channel as ch
    WHERE NOT EXISTS (
        SELECT 1
        FROM dialog d
        WHERE d.channel_id = ch.channel_id
    )""")
    connection.commit()

def delete_dialog(dialog_id: int, user_id: int):
    cur.execute("""
    DELETE
    FROM dialog
    WHERE dialog_id = %s AND
          user_id = %s
    """, (dialog_id, user_id))
    connection.commit()

def sync_dialogs(dialogs: list[tuple[Dialog, ForumTopic]], user_id: int):
    current_dialogs_set = {dialog[0].id for dialog in dialogs}
    cur.execute("""
    SELECT dialog_id
    FROM dialog
    WHERE user_id = %s
    """, (user_id, ))
    saved_dialogs = cur.fetchall()

    for dialog in saved_dialogs:
        if dialog.dialog_id not in current_dialogs_set:
            delete_dialog(dialog.dialog_id, user_id)
    delete_unused_channels()

# cant handle a lot of dialogs due to timeout
async def get_all_dialogs(user_id: int, topics=True):
    app_user = user_info[user_id]
    dialogs = []

    async for dialog in app_user.client.iter_dialogs():
        if topics and dialog.is_group and getattr(dialog.entity, "forum", False):
            result = await app_user.client(
                functions.messages.GetForumTopicsRequest(
                    peer=dialog,
                    offset_date=None,
                    offset_id=0,
                    offset_topic=0,
                    limit=100
                )
            )
            for topic in result.topics:
                if topic.unread_count == 0:
                    continue
                dialogs.append((dialog, topic))
            continue
        dialogs.append((dialog, None))
    sync_dialogs(dialogs, user_id)
    return dialogs

def update_dialog_priorities(user_id, dialog):
    cur.execute("""
    INSERT INTO dialog_priority (
        dialog_id,
        user_id,
        date_time)
    VALUES (%s, %s, %s)
    """, (dialog[0].id, user_id, dt.datetime.now()))

def delete_old_dialog_priorities(user_id):
    cur.execute("""
    DELETE
    FROM dialog_priority
    WHERE user_id = %s AND
        date_time <= (
        SELECT min(date_time)
        FROM (
            SELECT date_time
            FROM dialog_priority
            WHERE user_id = %s
            ORDER BY date_time desc
            LIMIT 100
        )
    )
    """, (user_id, user_id))
    connection.commit()

async def get_allowed_dialogs(user_id: int):
    app_user = user_info[user_id]
    if not app_user.allow_all and app_user.allowed_dialogs is not None:
        return app_user.allowed_dialogs

    all_dialogs = await get_all_dialogs(user_id)
    if app_user.allow_all:
        app_user.allowed_dialogs_set = set()
        return all_dialogs

    cur.execute("""
    SELECT *
    FROM dialog
    WHERE user_id = %s
      AND is_allowed = TRUE
    """, (user_id,))
    allowed_dialogs_set = {
        (dialog.dialog_id, get_db_topic_id(dialog))
        for dialog in
        cur.fetchall()
    }

    allowed_dialogs = []
    for dialog in all_dialogs:
        if (dialog[0].id, get_topic_id(dialog)) not in allowed_dialogs_set:
            continue
        allowed_dialogs.append(dialog)
    app_user.allowed_dialogs = allowed_dialogs
    app_user.allowed_dialogs_set = allowed_dialogs_set
    return allowed_dialogs

def get_unread_count(dialog):
    return dialog[1].unread_count if dialog[1] else dialog[0].unread_count

async def get_recent_dialogs(user_id: int):
    allowed_dialogs = await get_allowed_dialogs(user_id)

    cur.execute("""
    SELECT dialog_id, count(*) as count
    FROM (SELECT *
          FROM dialog_priority
          WHERE user_id = %s
          ORDER BY date_time desc
          LIMIT 100)
    GROUP BY dialog_id
    ORDER BY count(*)
    """, (user_id,))
    sorted_dialogs = cur.fetchall()
    dialog_to_index = {
        dialog.dialog_id: index
        for index, dialog in enumerate(sorted_dialogs)
    }
    unused_dialogs = []

    for dialog in allowed_dialogs:
        if dialog[0].id in dialog_to_index.keys():
            sorted_dialogs[dialog_to_index[dialog[0].id]] = dialog
        else:
            unused_dialogs.append(dialog)
    unused_dialogs.sort(reverse=True, key=lambda dialog: get_unread_count(dialog))
    sorted_dialogs += unused_dialogs
    return sorted_dialogs