import os

from dotenv import load_dotenv
from telethon.tl.types import Channel, ForumTopic

from llm import llm_client

load_dotenv()

async def create_embedding(text: str) -> list:
    response = await llm_client.embeddings.create(model=os.environ["EMBEDDING_MODEL"], input=text)
    return response.data[0].embedding
