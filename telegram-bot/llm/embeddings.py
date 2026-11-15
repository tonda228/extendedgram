from llm import llm_client

async def create_embedding(text: str, model: str) -> list[float]:
    response = await llm_client.embeddings.create(model=model, input=text)
    return response.data[0].embedding
