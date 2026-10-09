from llm.completions import send_data

async def process_search_request(data, text):
    message_text = "Give short answer on the following question: " + text

    result = await send_data(data, message_text)
    if len(result) > 1:
        message_text = "Use those sentences to give an answer on the following question: " + text
        result = await send_data(data, message_text)
    return result[0]