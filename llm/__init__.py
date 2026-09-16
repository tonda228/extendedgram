import os
import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

# Completions model API
URL = "http://localhost:12434/engines/v1/chat/completions"
COMPLETIONS_MODEL = "docker.io/ai/qwen3-vl:8B"

# OpenAI API
completions_client = AsyncOpenAI(base_url=os.environ["OPEN_AI_LOCAL_URL"])

# Embeddings model API

requests_client = httpx.AsyncClient(timeout=None)

