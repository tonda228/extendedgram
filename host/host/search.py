from typing import Any

import openai

from host.llm.completions import send_data, ProgressCallback

ANSWER_FORMAT = (
    "\nFormat the answer for Telegram using only <b>, <i>, and <blockquote> HTML tags when useful. "
    "Escape literal <, > and & as &lt;, &gt; and &amp;. "
    "Do not use Markdown syntax such as **bold**, *italics*, backticks, or > quote prefixes."
)

async def process_search_request(data: list[dict[str, Any]], text: str, progress: ProgressCallback | None = None) -> str | None:
    message_text = "Give short answer on the following question: " + text + ANSWER_FORMAT

    result = await send_data(data, message_text, progress=progress)
    if not result:
        return None
    if len(result) == 1:
        return result[0]
    message_text = "Combine the partial answers into one short answer to this question: " + text + ANSWER_FORMAT
    answers = [{"type": "text", "text": answer} for answer in result]
    try:
        merged = await send_data(answers, message_text, progress=progress, progress_stage="merging")
    except (ValueError, TypeError, openai.APIError):
        merged = []
    if 0 < len(merged) < len(result):
        result = merged
    return "\n\n".join(dict.fromkeys(result))
