from typing import Any


def get_request_data(data: list[dict[str, Any]], message_text: str) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": [{"type": "text", "text": message_text}]},
        {"role": "user", "content": data},
    ]
