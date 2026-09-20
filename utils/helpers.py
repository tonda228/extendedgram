import math
import os

async def accept_user(user_id, bot):
    await bot.send_message(chat_id=user_id, text="Your request have been accepted.")

async def reject_user(user_id, bot):
    await bot.send_message(chat_id=user_id, text="Your request have been rejected. If you think that's a mistake you can resend your request.")

def get_db_topic_id(dialog):
    return dialog.topic_id or 0

def get_topic_id(dialog):
    return dialog[1].id if dialog[1] else 0

def get_edit_message_text_func(message_id, context):
    async def func(chat_id, text, reply_markup):
        return await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup)
    return func

def get_user_name(user):
    names = []
    if user.username:
        names.append(user.username)
    if user.first_name:
        names.append(user.first_name)
    if user.last_name:
        names.append(user.last_name)
    user_name = " ".join(names)
    if len(names) == 0:
        return "unknown"
    return user_name


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

def prev_page(app_user, length):
    pages_count = math.ceil(length / int(os.environ["PAGE_SIZE"]))
    app_user.cur_page -= 1
    if app_user.cur_page < 0:
        app_user.cur_page = pages_count - 1

def next_page(app_user, length):
    pages_count = math.ceil(length / int(os.environ["PAGE_SIZE"]))
    app_user.cur_page = (app_user.cur_page + 1) % pages_count