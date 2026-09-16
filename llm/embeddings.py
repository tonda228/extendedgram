import os

from dotenv import load_dotenv
from pgvector import Vector
from telethon.tl.custom import Message, Dialog
from telethon.tl.types import Channel, ForumTopic

from database import cur, connection
from llm import requests_client

load_dotenv()

async def create_embedding(text: str) -> Vector:
    headers = {
        "Content-Type": "application/json"
    }
    payload = {
        "model": os.environ["EMBEDDING_MODEL"],
        "input": text
    }
    # change to openai api
    response = await requests_client.post(os.environ["EMBEDDING_LOCAL_URL"], headers=headers, json=payload)
    return Vector(response.json()["data"][0]["embedding"])

async def create_message_embedding(dialog: tuple[Dialog, ForumTopic], message: Message, user_id: int, media_description: str | None, update: bool = False) -> Vector:
    text = "Text: " + ("None" if message.text is None else message.text)
    media = "Media: " + ("None" if media_description is None else media_description)
    full_text = text + "\n" + media

    embedding = await create_embedding(full_text)

    if update:
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