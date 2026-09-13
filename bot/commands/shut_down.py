from telegram import Update
from telegram.ext import ContextTypes

from features.preloading import reset_idle_timer
from state import user_info
import infinite_task


async def shut_down(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    app_user = user_info[user_id]
    if not app_user.is_admin:
        return

    print("hello?")

    reset_idle_timer(user_id, False)

    # store user states
    # let users know that the bot was stopped
    # also delete media

    if infinite_task.infinite_task:
        infinite_task.infinite_task.cancel()

