import httpx
from pgvector import Vector
from telethon.tl.custom import Message
from telethon.tl.types import User, Channel

from database import cur, connection
from llm import EMBEDDING_MODEL, requests_client, EMBEDDING_URL


async def create_embedding(text):
    headers = {
        "Content-Type": "application/json"
    }
    payload = {
        "model": EMBEDDING_MODEL,
        "input": text
    }
    response = await requests_client.post(EMBEDDING_URL, headers=headers, json=payload)
    return Vector(response.json()["data"][0]["embedding"])

async def create_message_embedding(dialog, message: Message, user_id: int, media_description: str | None, update: bool = False) -> Vector:
    text = "Text: " + ("None" if message.text is None else message.text)
    media = "Media: " + ("None" if media_description is None else media_description)
    full_text = text + "\n" + media

    embedding = await create_embedding(full_text)

    if update:
        #maybe write in one transaction???
        if isinstance(dialog[0], Channel):
            topic_id = dialog[1].id if dialog[1] else 0
            cur.execute("""
            UPDATE public_message
            SET embedding = %s
            WHERE message_id = %s and
                  channel_id = %s and
                  topic_id = %s
            """, (embedding, message.id, dialog[0].id, topic_id))
        else:
            cur.execute("""
            UPDATE private_message
            SET embedding = %s
            WHERE message_id = %s and
                  dialog_id = %s and
                  user_id = %s
            """, (embedding, message.id, dialog[0].id, user_id))
        connection.commit()
    return embedding