from telethon import events, TelegramClient
from telethon.tl.custom import Dialog, Message
from telethon.tl.functions.messages import GetForumTopicsRequest

from database.dialogs import store_dialog
from database.messages import store_message, delete_public_message, delete_private_message
from utils.state import user_info
from telethon.tl.types import User, Channel, ForumTopic, UpdateDeleteMessages, UpdateDeleteChannelMessages

async def get_topic(channel: Dialog, message: Message, client: TelegramClient) -> ForumTopic | None:
    if not getattr(channel, "forum", False) or not message.reply_to:
        return None
    topic_id = message.reply_to.reply_to_top_id or message.reply_to.reply_to_msg_id

    result = await client(
        GetForumTopicsRequest(
            peer=channel,
            offset_date=None,
            offset_id=0,
            offset_topic=0,
            limit=100
        )
    )
    for topic in result.topics:
        if topic.id == topic_id:
            return topic

    return None

async def create_new_message_handler(user_id: int):
    client = user_info[user_id].client

    @client.on(events.NewMessage)
    async def new_message_handler(event: events.NewMessage.Event):
        dialog = await event.get_chat()
        message = event.message
        topic = await get_topic(dialog, message, client)

        store_dialog((dialog, topic), user_id)
        await store_message(message, (dialog, topic), user_id, True)



async def create_delete_message_handler(user_id: int):
    client = user_info[user_id].client

    @client.on(events.MessageDeleted)
    async def delete_message_handler(event: events.MessageDeleted.Event):
        if isinstance(event.original_update, UpdateDeleteChannelMessages):
            for msg_id in event.deleted_ids:
                delete_public_message(event.original_update.channel_id, msg_id)
        else:
            for msg_id in event.deleted_ids:
                delete_private_message(user_id, msg_id)


