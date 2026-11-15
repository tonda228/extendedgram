import os
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

# client for OpenAI compatible endpoints
llm_client = AsyncOpenAI(base_url=os.environ["OPEN_AI_LOCAL_URL"])
