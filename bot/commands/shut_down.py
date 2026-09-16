from telegram import Update
from telegram.ext import ContextTypes

from features.preloading import reset_idle_timer
from utils import infinite_task
from utils.state import user_info


async def shut_down(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    app_user = user_info[user_id]
    if not app_user.is_admin:
        return

    reset_idle_timer(user_id, False)

    if infinite_task.infinite_task:
        infinite_task.infinite_task.cancel()

