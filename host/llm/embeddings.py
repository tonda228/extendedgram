import openai

from host.llm import llm_client

async def create_embedding(text: str, model: str) -> list[float]:
    response = await llm_client.embeddings.create(model=model, input=text)
    return response.data[0].embedding


async def create_embeddings(texts: list[str], model: str) -> list[list[float]]:
    if not texts:
        return []
    try:
        response = await llm_client.embeddings.create(model=model, input=texts)
    except openai.APIStatusError as error:
        size_error = error.status_code == 413 or (error.status_code in (400, 422) and (
            error.code in ("context_length_exceeded", "max_tokens_per_request", "batch_size_exceeded") or
            any(term in str(error).lower() for term in (
                "context length", "token limit", "too many inputs", "batch size", "input too long", "request too large",
            ))
        ))
        if not size_error or len(texts) <= 1:
            raise
        midpoint = len(texts) // 2
        left = await create_embeddings(texts[:midpoint], model)
        right = await create_embeddings(texts[midpoint:], model)
        return left + right
    ordered = sorted(response.data, key=lambda item: item.index)
    if [item.index for item in ordered] != list(range(len(texts))):
        raise ValueError("The embedding model returned an incomplete or invalid batch")
    return [item.embedding for item in ordered]
