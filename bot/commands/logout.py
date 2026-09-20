from telegram import InlineKeyboardButton, Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import connection, cur
from database.dialogs import delete_unused_channels
from utils.classes import UserState
from utils.state import user_info


async def log_out_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton(text="Yes, I do.", callback_data="Yes"),
            InlineKeyboardButton(text="No, I do not.", callback_data="No")
        ]
    ]
    msg = await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text="Do you wish to log out and delete all your data?",
                                   reply_markup=InlineKeyboardMarkup(keyboard))
    user_info[update.effective_user.id].status = UserState.WAIT_FOR_LOG_OUT_CONFIRMATION
    user_info[update.effective_user.id].message_id = msg.id

async def log_out_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur.execute("""
    DELETE
    FROM app_user
    WHERE user_id = %s;
    """, (update.effective_user.id,))

    delete_unused_channels()

    cur.execute("""
    --delete telegram_users that don't have messages
    DELETE 
    FROM telegram_user as tu
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
    """)
    connection.commit()

    if update.effective_user.id in user_info:
        await user_info[update.effective_user.id].client.log_out()
        del user_info[update.effective_user.id]
    await context.bot.send_message(chat_id=update.effective_chat.id, text="You've successfully logged out.")