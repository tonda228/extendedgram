import os

from dotenv import load_dotenv
from pgvector import Vector
from telethon.tl.custom import Message, Dialog
from telethon.tl.types import Channel, ForumTopic

from database import cur, connection
from llm import llm_client

load_dotenv()

async def create_embedding(text: str) -> list:
    response = await llm_client.embeddings.create(model=os.environ["EMBEDDING_MODEL"], input=text)
    return response.data[0].embedding