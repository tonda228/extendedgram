def get_full_chat_name(dialog):
    chat_name = f"{dialog[0].title}"
    if dialog[1]:
        chat_name += f"|{dialog[1].title}"
    return chat_name


def get_message_info(data, message):
    if message.user_name:
         name = "Username: " + message.user_name
    else:
        name = message.title
    data.append({
        "type": "text",
        "text": name
    })

    message_id = str(message.message_id)
    date =  message.date_time.strftime("%H:%M:%S %d.%m.%Y")
    message_text = "Message " + message_id + " " + date + ": " + (message.text if message.text else "")
    media_text = "Media: " + ("None" if message.media_description is None else message.media_description)
    full_text = message_text + "\n" + media_text
    data.append({
        "type": "text",
        "text": full_text
    })