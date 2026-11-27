from typing import Any

import json
import openai
from pydantic import TypeAdapter
from host.utils.classes import SummaryTopic
from host.llm.completions import send_data, ProgressCallback


MAX_TOPICS = 10
TOPICS = TypeAdapter(list[SummaryTopic])


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


    topics = TOPICS.validate_python(await send_data(data, message_text, True, 5, progress=progress))
    stalled = False

    for _ in range(5):
        if len(topics) <= MAX_TOPICS:
            break
        limit = MAX_TOPICS // 2 if stalled else MAX_TOPICS
        message_text = (
            f"Select or combine the most important topics. Return no more than {limit} topics in total per answer. "
            "Omit minor topics and combine related ones. Do not repeat every input topic. "
            "Keep the original message IDs and language." + format_text
        )
        merge_data = [
            {"type": "text", "text": json.dumps(topic.model_dump(), ensure_ascii=False)}
            for topic in topics
        ]
        try:
            merged = TOPICS.validate_python(await send_data(
                merge_data, message_text, for_summary=True, tries_left=2,
                progress=progress, progress_stage="merging"))
        except (ValueError, TypeError, openai.APIError):
            # The optional merge failed; the last valid summaries are still usable.
            break
        if not merged or len(merged) >= len(topics):
            if stalled:
                break
            stalled = True
            continue
        topics = merged
        stalled = False

    if len(topics) > MAX_TOPICS:
        # Keep topics across the conversation instead of taking only the earliest ones.
        topics = sorted(topics, key=lambda topic: topic.start_message_id)
        topics = [topics[i * (len(topics) - 1) // (MAX_TOPICS - 1)] for i in range(MAX_TOPICS)]
    return [topic.model_dump() for topic in topics]
