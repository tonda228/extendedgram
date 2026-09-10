from telegram import InlineKeyboardButton, Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from classes import UserState
from database import connection, cur
from state import user_info


async def log_out_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton(text="Yes, I do.", callback_data="Yes"),
            InlineKeyboardButton(text="No, I do not.", callback_data="No")
        ]
    ]
    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text="Do you wish to log out and delete all your data?",
                                   reply_markup=InlineKeyboardMarkup(keyboard))
    user_info[update.effective_user.id].status = UserState.WAIT_FOR_LOG_OUT_CONFIRMATION

async def log_out_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur.execute("""
    DELETE
    FROM app_user
    WHERE user_id = %s;

    --delete channels that don't have dialogs connected to them
    DELETE
    FROM channel as ch
    WHERE NOT EXISTS (
        SELECT 1
        FROM dialog d
        WHERE d.channel_id = ch.channel_id
    );

    --delete telegram_users that don't have messages
    DELETE 
    FROM telegram_users as tu
    WHERE NOT EXISTS (
        SELECT 1
        FROM app_user au
        WHERE tu.user_id = au.user_id
    ) AND NOT EXISTS (
        SELECT 1
        FROM public_message pb
        WHERE tu.user_id = pb.sender_id
    ) AND NOT EXISTS (
        SELECT 1
        FROM private_message pb
        WHERE tu.user_id = pb.user_id
    )
    """, (update.effective_user.id,))
    connection.commit()

    if update.effective_user.id in user_info:
        del user_info[update.effective_user.id]
    await context.bot.send_message(chat_id=update.effective_chat.id, text="You've successfully logged out.")