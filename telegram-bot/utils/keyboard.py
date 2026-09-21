from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from utils.state import user_info


def update_inline_button(button, data, new_state=None, save_to=None):
    if button.callback_data != data:
        return InlineKeyboardButton(text=button.text, callback_data=button.callback_data)

    split_text = button.text.split(": ")
    if not new_state:
        new_state = split_text[-1] == "❌"

    if save_to is not None:
        if new_state:
            save_to.add(data)
        else:
            save_to.remove(data)
    new_text = "".join(button.text.split(":")[:-1]) + ": " + ("✅" if new_state else "❌")
    return InlineKeyboardButton(text=new_text, callback_data=data)


def update_inline_keyboard(keyboard, data, new_state=None, save_to=None):
    copy = []
    for i, row in enumerate(keyboard):
        copy.append(list())
        for button in row:
            copy[i].append(update_inline_button(button, data, new_state, save_to))
    return InlineKeyboardMarkup(copy)

async def send_updated_inline_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE, query, new_state=None, save_to=None):
    keyboard = query.message.reply_markup.inline_keyboard
    new_markup = update_inline_keyboard(keyboard, query.data, new_state=new_state, save_to=save_to)

    msg = await context.bot.edit_message_reply_markup(chat_id=update.effective_chat.id,
                                                message_id=query.message.message_id,
                                                reply_markup=new_markup)
    user_info[update.effective_user.id].message_id = msg.id