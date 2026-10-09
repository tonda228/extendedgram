from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from database import connection, cur
from database.dialogs import delete_unused_channels
from utils.classes import UserState
from utils.helpers import send_message
from utils.state import user_info


async def log_out_request(update: Update|None=None, context: ContextTypes.DEFAULT_TYPE|None=None, user_id=None):
    if user_id is None:
        user_id = update.effective_user.id
    keyboard = [
        [
            InlineKeyboardButton(text="Yes, I do.", callback_data="Yes"),
            InlineKeyboardButton(text="No, I do not.", callback_data="No")
        ]
    ]
    msg = await send_message(chat_id=user_id,
                                   text="Do you wish to log out and delete all your data?",
                                   reply_markup=InlineKeyboardMarkup(keyboard))
    user_info[user_id].status = UserState.WAIT_FOR_LOG_OUT_CONFIRMATION
    user_info[user_id].message_id = msg.id

async def log_out_confirmation(user_id):
    cur.execute("""
    DELETE
    FROM app_user
    WHERE user_id = %s;
    """, (user_id,))

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

    if user_id in user_info:
        await user_info[user_id].client.log_out()
        del user_info[user_id]
    await send_message(chat_id=user_id, text="You've successfully logged out.")