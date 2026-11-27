from telegram import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message
from telegram.error import BadRequest, TelegramError

from bot import application
from utils.state import user_info


async def disable_inline_keyboard(user_id: int) -> None:
    user = user_info[user_id]
    message = user.last_message
    # Invalidate first so a second click cannot start the same operation again.
    user.last_message = None
    if message is not None:
        try:
            await application.bot.delete_message(chat_id=user_id, message_id=message.message_id)
        except TelegramError:
            try:
                await application.bot.edit_message_reply_markup(chat_id=user_id, message_id=message.message_id,
                                                               reply_markup=None)
            except TelegramError:
                pass  # The stale-message check still prevents any action.


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
    new_text = button.text.rsplit(":", 1)[0] + ": " + ("✅" if new_state else "❌")
    return InlineKeyboardButton(text=new_text, callback_data=data)


def update_inline_keyboard(keyboard: tuple[tuple[InlineKeyboardButton, ...], ...], data: str, save_to: set[str] | None=None) -> InlineKeyboardMarkup:
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

    try:
        msg = await application.bot.edit_message_reply_markup(chat_id=user_id,
                                                message_id=message.message_id,
                                                reply_markup=new_markup)
    except BadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
        return
    if isinstance(msg, Message):
        user_info[user_id].last_message = msg
