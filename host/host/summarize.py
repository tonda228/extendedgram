from typing import Any

import json
from pydantic import TypeAdapter
from host.utils.classes import SummaryTopic
from host.llm.completions import send_data, ProgressCallback


async def process_summarize_request(data: list[dict[str, Any]], progress: ProgressCallback | None = None) -> list[dict[str, Any]]:
    response_format = """
        [
            {
                "start_message_id": 123,
                "end_message_id": 456,
                "topic": "...",
                "summary": "..."
            }
        ]
        """
    format_text = f"\nResponse give in json in following format:\n {response_format}"
    message_text = ("Summarize the messages very concisely. "
                    "For each message, you first receive the sender name and then the message. "
                    "Group related messages into topics, but do not merge unrelated conversations. "
                    "For each topic, include the first and last message IDs. "
                    "Return at most 10 topics. "
                    "Prioritize only important information, decisions, questions, plans, and conclusions. "
                    "Ignore greetings, repetition, jokes, filler, and minor details unless they are necessary to understand the topic. "
                    "For every 20 input messages, produce approximately 1 topic summary when possible. "
                    "Each topic summary should normally be 1-5 sentences and no more than 150 words. "
                    "Use a surface-level summary only: do not retell the conversation message by message. "
                    "Do not include details that are not essential. "
                    "If several messages repeat the same idea, mention it only once. "
                    "Respond in the same language as the messages you received "
                    "If the conversation is short or contains little important information, return fewer topics rather than adding detail. "
                    "Add information about who says what if that person talks about his situation" + format_text)


    results = await send_data(data, message_text, True, 5, progress=progress)
    topics = TypeAdapter(list[SummaryTopic]).validate_python(results)
    results = [topic.model_dump() for topic in topics]

    for _ in range(5):
        if len(results) <= 10:
            return results
        previous_count = len(results)
        message_text = "Combine those topics. Leave only 10 topics." + format_text
        data = [
            {"type": "text", "text": json.dumps(result)}
            for result in results
        ]
        results = await send_data(data, message_text, for_summary=True, progress=progress, progress_stage="merging")
        topics = TypeAdapter(list[SummaryTopic]).validate_python(results)
        results = [topic.model_dump() for topic in topics]
        if len(results) >= previous_count:
            raise ValueError("The model did not reduce the number of topics")

    if len(results) <= 10:
        return results
    raise ValueError("Could not combine the summary into ten topics")
