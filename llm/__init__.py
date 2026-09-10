import os
import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

# Completions model API
URL = "http://localhost:12434/engines/v1/chat/completions"
COMPLETIONS_MODEL = "docker.io/ai/qwen3-vl:8B"

# OpenAI API
OPEN_AI_URL = "http://localhost:12434/engines/v1/"
completions_client = AsyncOpenAI(base_url=OPEN_AI_URL)

# Embeddings model API
EMBEDDING_MODEL = "ai/qwen3-embedding:0.6b"
EMBEDDING_URL = "http://localhost:12434/engines/v1/embeddings"

requests_client = httpx.AsyncClient(timeout=None)

