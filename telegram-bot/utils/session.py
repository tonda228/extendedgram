import asyncio
from logging import getLogger
from time import monotonic

from features.preloading import reset_idle_timer
from server import available_hosts
from utils.classes import UserState
from utils.helpers import send_message
from utils.keyboard import disable_inline_keyboard
from utils.state import user_info

SESSION_IDLE_TIMEOUT = 30 * 60
SESSION_CHECK_INTERVAL = 30
logger = getLogger(__name__)


def record_activity(user_id: int) -> None:
    user = user_info.get(user_id)
    if user is not None:
        user.last_activity = monotonic()


async def expire_idle_session(user_id: int) -> bool:
    user = user_info.get(user_id)
    if user is None or user.status < UserState.AUTHENTICATED:
        return False
    host = available_hosts.get(user_id)
    if host is not None and not host.disconnected:
        return False
    # Let a foreground search or summary finish before starting its idle period.
    if user.search_in_progress or user.status == UserState.WAIT_FOR_REQUEST_ANSWER:
        record_activity(user_id)
        return False
    if monotonic() - user.last_activity < SESSION_IDLE_TIMEOUT:
        return False

    user.status = UserState.LOGGED_OUT
    client = user.client
    user.client = None
    user.pairing_code_hash = None
    user.pairing_code_expires_at = 0.0
    user.pending_search_query = None
    user.search_since = None
    user.search_until = None
    user.dialogs = None
    user.chosen_ids = None
    user.pending_group = None
    user.pending_group_name = None
    user.pending_group_password_hash = None
    reset_idle_timer(user_id, False)
    await disable_inline_keyboard(user_id)
    if client is not None:
        try:
            await asyncio.wait_for(client.disconnect(), timeout=5)
        except Exception:
            logger.exception("Could not disconnect expired Telegram session for user %s", user_id)
    if user_info.get(user_id) is user and user.status == UserState.LOGGED_OUT:
        await send_message(user_id, "Your session expired after 30 minutes of inactivity. Please enter your bot password.")
    return True


async def watch_idle_sessions() -> None:
    while True:
        await asyncio.sleep(SESSION_CHECK_INTERVAL)
        for user_id in list(user_info):
            try:
                await expire_idle_session(user_id)
            except Exception:
                logger.exception("Could not expire idle session for user %s", user_id)
