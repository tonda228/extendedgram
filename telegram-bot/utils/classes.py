import asyncio
from enum import IntEnum, auto

from starlette.websockets import WebSocket
from telethon.client import telegramclient


class UserState(IntEnum):
    LOGGED_OUT = auto()
    WAIT_FOR_AUTHENTICATION_CHOICE = auto()
    AUTHENTICATED = auto()
    WAIT_FOR_MENU_CHOICE = auto()
    WAIT_FOR_SUMMARIZE_CHAT = auto()
    WAIT_FOR_READ = auto()
    WAIT_FOR_SEARCH_CHAT = auto()
    WAIT_FOR_SEARCH_TEXT = auto()
    WAIT_FOR_SETTINGS_CHOICE = auto()
    WAIT_FOR_CHANGE_ALLOWED_DIALOGS_CONFIRMATION = auto()
    WAIT_FOR_NEW_ALLOWED_DIALOGS_OPTIONS_CHOICE = auto()
    WAIT_FOR_ALLOWED_DIALOGS_CATEGORY_CHOICE = auto()
    WAIT_FOR_ALLOWED_DIALOGS_MANUAL_CHOICE = auto()
    WAIT_FOR_HISTORY_SIZE_CHANGE_CONFIRMATION = auto()
    WAIT_FOR_NEW_HISTORY_SIZE = auto()
    WAIT_FOR_LOG_OUT_CONFIRMATION = auto()
    CANCEL_OPERATION = auto()
    WAIT_FOR_REQUEST_ANSWER = auto()

class LoginStage(IntEnum):
    AWAIT_ROLE_CHOICE = auto()
    AWAIT_ROLE_TYPE_CHOICE = auto()
    AWAIT_PHONE_NUMBER = auto()
    AWAIT_CODE = auto()
    AWAIT_TELEGRAM_PASSWORD = auto()
    AWAIT_BOT_PASSWORD = auto()
    AWAIT_BOT_PASSWORD_CONFIRMATION = auto()
    AWAIT_IS_READY = auto()
    AWAIT_GROUP_CHOICE = auto()
    AWAIT_NEW_GROUP_NAME = auto()
    AWAIT_NEW_GROUP_PASSWORD = auto()
    AWAIT_NEW_GROUP_PASSWORD_CONFIRMATION = auto()
    AWAIT_GROUP_NAME = auto()
    AWAIT_GROUP_PASSWORD = auto()

class InitializationInfo:
    def __init__(self):
        self.phone_num = None
        self.phone_code_hash = None
        self.tries_left = 5
        self.wait_task = None

class AppUser:
    def __init__(self, user_id, status: UserState, client: telegramclient.TelegramClient|None=None, app_user=None):
        self.user_id = user_id
        self.status = status
        self.client = client
        self.dialogs = None
        self.chosen_ids = None
        self.allowed_dialogs_options = None
        self.last_read = None
        self.allowed_dialogs = None
        self.cur_page = 0
        self.last_category = None
        self.category_dialogs_count = None
        self.allowed_dialogs_set = None
        self.message_id = None
        self.last_message = None
        self.pairing_code_hash = None

        self.string_session = None if not app_user else app_user.string_session
        self.preloading = False if not app_user else app_user.preloading
        self.history_size = 0 if not app_user else app_user.history_size
        self.set_read_after_summary = False if not app_user else app_user.set_read_after_summary
        self.allow_all = True if not app_user else app_user.allow_all
        self.is_host = False if not app_user else app_user.is_host
        self.order_by = "date" if not app_user or app_user.order_by is None else app_user.order_by
        self.group_id = None if not app_user else app_user.group_id
        self.password_hash = None if not app_user else app_user.token_hash
        self.token_hash = None if not app_user else app_user.token_hash

class Host:
    def __init__(self, user, websocket):
        self.user = user
        self.websocket: WebSocket = websocket

class Request:
    def __init__(self, user_id: int, host: Host, future, message=None, dialog=None, update=False):
        self.user_id = user_id,
        self.host = host
        self.future = future
        self.message = message
        self.dialog = dialog
        self.update = update


class AppUserPreloading:
    def __init__(self):
        self.idle_task = None
        self.preloading = None

class UninitializedUser:
    def __init__(self):
        self.status = LoginStage.AWAIT_ROLE_CHOICE
        self.is_host = False
        self.in_group = False
        self.bot_password = None
        self._init_info = InitializationInfo()

        self.group_name = None
        self.hashed_password = None
        self.group = None

    @property
    def phone_num(self):
        return self._init_info.phone_num if self._init_info else None

    @property
    def phone_code_hash(self):
        return self._init_info.phone_code_hash if self._init_info else None

    @property
    def tries_left(self):
        return self._init_info.tries_left if self._init_info else None

    @phone_num.setter
    def phone_num(self, val):
        self._init_info.phone_num = val

    @phone_code_hash.setter
    def phone_code_hash(self, val):
        self._init_info.phone_code_hash = val

    @tries_left.setter
    def tries_left(self, val):
        if val <= 0:
            self._init_info.wait_task = asyncio.sleep(300)
        else:
            self._init_info.wait_task = None
        self._init_info.tries_left = val

    @property
    def wait(self):
        return self._init_info.wait_task.done() if self._init_info and self._init_info.wait_task else False
