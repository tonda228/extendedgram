from collections.abc import Awaitable, Callable
from typing import Any

import json
import os

import openai
from pydantic import TypeAdapter, ValidationError

from host.llm import llm_client
from host.utils.helpers import get_request_data
from host.utils.classes import SummaryTopic


async def translate_image(image_data: str) -> str | None:
    request_data = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "Give very concise one sentence description of this image"
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": image_data
                }
            }
        ]
    }
    response = await llm_client.chat.completions.create(
        model=os.environ["COMPLETIONS_MODEL"],
        messages=[request_data])
    media_description = response.choices[0].message.content
    return media_description

ProgressCallback = Callable[[int, int, str], Awaitable[None]]


async def send_data(data: list[dict[str, Any]],
                    message_text: str,
                    for_summary: bool = False,
                    tries_left: int = 5,
                    progress: ProgressCallback | None = None,
                    progress_stage: str = "messages") -> list[Any]:
    if progress is not None:
        await progress(0, max(1, len(data)), progress_stage)
    results = await _send_batch(data, message_text, for_summary, tries_left)
    if progress is not None:
        await progress(max(1, len(data)), max(1, len(data)), progress_stage)
    return results


async def _send_batch(data: list[dict[str, Any]], message_text: str,
                      for_summary: bool, tries_left: int) -> list[Any]:
    while True:
        try:
            request_data = get_request_data(data, message_text=message_text)
            response = await llm_client.chat.completions.create(
                model=os.environ["COMPLETIONS_MODEL"], messages=request_data)
            choice = response.choices[0]
            if getattr(choice, "finish_reason", None) == "length":
                if len(data) <= 1:
                    raise ValueError("The model truncated its answer for a single message")
                mid = len(data) // 2
                left = await _send_batch(data[:mid], message_text, for_summary, tries_left)
                right = await _send_batch(data[mid:], message_text, for_summary, tries_left)
                return left + right
            new_data = choice.message.content
            if for_summary:
                parsed = json.loads(new_data)
                try:
                    topics = TypeAdapter(list[SummaryTopic]).validate_python(parsed)
                except ValidationError:
                    tries_left -= 1
                    if tries_left <= 0:
                        raise
                    continue
                return [topic.model_dump() for topic in topics]
            if not isinstance(new_data, str) or not new_data.strip():
                raise ValueError("The model returned an empty response")
            return [new_data]
        except json.decoder.JSONDecodeError:
            tries_left -= 1
            if tries_left <= 0:
                raise
        except openai.BadRequestError as error:
            context_error = error.code == "context_length_exceeded" or "context" in str(error).lower()
            if len(data) <= 1 or not context_error:
                raise
            mid = len(data) // 2
            left = await _send_batch(data[:mid], message_text, for_summary, tries_left)
            right = await _send_batch(data[mid:], message_text, for_summary, tries_left)
            return left + right
