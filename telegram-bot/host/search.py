from typing import Any

from llm.completions import send_data, ProgressCallback

async def process_search_request(data: list[dict[str, Any]], text: str, progress: ProgressCallback | None = None) -> str | None:
    message_text = "Give short answer on the following question: " + text

    result = await send_data(data, message_text, progress=progress)
    for _ in range(5):
        if len(result) == 1:
            return result[0]
        if not result:
            raise ValueError("The model returned no answers")
        previous_count = len(result)
        message_text = "Use those sentences to give an answer on the following question: " + text
        answers = [{"type": "text", "text": answer} for answer in result]
        result = await send_data(answers, message_text, progress=progress, progress_stage="merging")
        if len(result) >= previous_count:
            raise ValueError("The model did not combine partial answers")
    if len(result) == 1:
        return result[0]
    raise ValueError("Could not combine the partial answers")
