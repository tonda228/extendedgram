from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message

from bot import application
from utils.state import user_info


def update_inline_button(button: InlineKeyboardButton, data: str, save_to: set[str] | None=None) -> InlineKeyboardButton:
    if button.callback_data != data:
        return InlineKeyboardButton(text=button.text, callback_data=button.callback_data)

    split_text = button.text.split(": ")
    new_state = split_text[-1] == "❌"

    if save_to is not None:
        if new_state:
            save_to.add(data)
        else:
            save_to.discard(data)
    new_text = "".join(button.text.split(":")[:-1]) + ": " + ("✅" if new_state else "❌")
    return InlineKeyboardButton(text=new_text, callback_data=data)


def update_inline_keyboard(keyboard: tuple[tuple[InlineKeyboardButton, ...], ...], data: list[dict[str, Any]], save_to: set[str] | None=None) -> InlineKeyboardMarkup:
    copy = []
    for i, row in enumerate(keyboard):
        copy.append(list())
        for button in row:
            copy[i].append(update_inline_button(button, data, save_to))
    return InlineKeyboardMarkup(copy)

async def send_updated_inline_keyboard(user_id: int, query: CallbackQuery, save_to: set[str] | None=None) -> None:
    message = query.message
    if message is None or not isinstance(message, Message) or message.reply_markup is None:
        return
    keyboard = message.reply_markup.inline_keyboard
    new_markup = update_inline_keyboard(keyboard, query.data, save_to=save_to)

    msg = await application.bot.edit_message_reply_markup(chat_id=user_id,
                                                message_id=message.message_id,
                                                reply_markup=new_markup)
    user_info[user_id].last_message = msg
