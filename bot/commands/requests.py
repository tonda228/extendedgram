from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot.commands.menu import menu
from database import cur, connection
from utils.check_authentication import check_authentication, query_phone_number
from utils.classes import UserState
from utils.helpers import accept_user, reject_user
from utils.state import user_info


async def get_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_authentication(update, context):
        return
    user_id = update.effective_user.id
    app_user = user_info[user_id]
    if not app_user.is_admin:
        return
    cur.execute("""
    SELECT *
    FROM user_request
    JOIN telegram_user USING (user_id)
    WHERE NOT is_resolved
    """)

    result = cur.fetchall()
    if len(result) == 0:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="There are no new requests")
        await menu(update, context)
        return

    for user in result:
        user_name = user.user_name
        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(text="Accept", callback_data=f"accept {user.user_id}"),
                InlineKeyboardButton(text="Reject", callback_data=f"reject {user.user_id}")
            ]
        ])
        await context.bot.send_message(chat_id=update.effective_chat.id, text=user_name, reply_markup=markup)
    app_user.status = UserState.WAIT_FOR_REQUEST_ANSWER

async def resolve_request(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id: int, user_id: int, is_accepted: bool):
    cur.execute("""
    UPDATE user_request
    SET is_resolved = TRUE,
        is_accepted = %s,
        resolved_by = %s
    WHERE user_id = %s AND
          -- preventing conflicts with other admins
          is_resolved = FALSE
    RETURNING *
    """, (is_accepted, admin_id, user_id))
    connection.commit()
    user_request = cur.fetchone()

    if user_request is None:
        return

    if is_accepted:
        await accept_user(user_request.user_id, context.bot)
    else:
        await reject_user(user_request.user_id, context.bot)
    cur.execute("""
    UPDATE user_request
    SET is_user_notified = TRUE
    WHERE user_id = %s
    """, (user_id,))
    connection.commit()
    if is_accepted:
        await query_phone_number(user_request.user_id, context.bot)
