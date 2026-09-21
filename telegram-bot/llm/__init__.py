import os
import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

# Completions model API
URL = "http://localhost:12434/engines/v1/chat/completions"
COMPLETIONS_MODEL = "docker.io/ai/qwen3-vl:8B"

# client for OpenAI compatible endpoints
llm_client = AsyncOpenAI(base_url=os.environ["OPEN_AI_URL"])

