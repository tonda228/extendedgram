import datetime as dt
from telethon.tl.types import User, Channel
from telethon import functions

from state import user_info
from . import cur, connection


def store_channel(channel: Channel):
    topic_id = channel[1].id if channel[1] else None
    topic_name = channel[1].title if channel[1] else None

    cur.execute("""
    INSERT INTO channel (channel_id, topic_id, topic_name)
    VALUES (%s, %s, %s) ON CONFLICT (channel_id, topic_id) DO UPDATE
        SET topic_name = EXCLUDED.topic_name
    """, (channel[0].id, topic_id, topic_name))

def store_dialog(dialog, user_id):
    channel_id = dialog[0].id if isinstance(dialog[0], Channel) else None
    topic_id = dialog[1].id if dialog[1] else None

    if isinstance(dialog[0], Channel):
        store_channel(dialog)

    cur.execute("""
    INSERT INTO dialog (dialog_id, user_id, title, is_allowed, channel_id, topic_id)
    VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (dialog_id, user_id) DO UPDATE
        SET title = EXCLUDED.title,
            is_allowed = EXCLUDED.is_allowed
    """, (dialog[0].id, user_id, dialog[0].title, True, channel_id, topic_id))


# cant handle a lot of dialogs due to timeout
async def get_all_dialogs(user_id: int):
    app_user = user_info[user_id]
    dialogs = []
    index = 1
    async for dialog in app_user.client.iter_dialogs(limit=50):
        if dialog.is_group and getattr(dialog.entity, "forum", False):
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
                index += 1
            continue
        dialogs.append((dialog, None))
        index += 1
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
    all_dialogs = await get_all_dialogs(user_id)
    if app_user.allow_all:
        return all_dialogs

    cur.execute("""
    SELECT *
    FROM dialog
    WHERE user_id = %s
      AND is_allowed = TRUE
    """, (user_id,))
    allowed_dialogs_set = {
        dialog.dialog_id
        for dialog in
        cur.fetchall()
    }

    allowed_dialogs = []
    for dialog in all_dialogs:
        if dialog[0].id not in allowed_dialogs_set:
            continue
        allowed_dialogs.append(dialog)
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