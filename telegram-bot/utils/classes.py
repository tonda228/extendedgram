import asyncio
from datetime import datetime
from time import monotonic
from enum import IntEnum, auto

from numpy import ndarray
from pgvector import Vector
from starlette.websockets import WebSocket
from telegram import Message as TelegramMessage
from telethon import TelegramClient
from telethon.tl.types import User, Channel, ForumTopic, UpdateDeleteMessages, UpdateDeleteChannelMessages
from pydantic import BaseModel, PrivateAttr, ConfigDict, Field
from telethon.tl.custom import Dialog, Message


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
    WAIT_FOR_GROUP_CHOICE = auto()
    WAIT_FOR_NEW_GROUP_NAME = auto()
    WAIT_FOR_NEW_GROUP_PASSWORD = auto()
    WAIT_FOR_NEW_GROUP_PASSWORD_CONFIRMATION = auto()
    WAIT_FOR_GROUP_NAME = auto()
    WAIT_FOR_GROUP_PASSWORD = auto()
    WAIT_FOR_GROUP_SETTINGS = auto()
    WAIT_FOR_NEW_GROUP_EMBEDDING_MODEL = auto()
    WAIT_FOR_GROUP_EMBEDDING_MODEL = auto()
    WAIT_FOR_GROUP_DELETE_CONFIRMATION = auto()
    WAIT_FOR_SEARCH_DATES = auto()
    WAIT_FOR_SEARCH_PERIOD = auto()

class LoginStage(IntEnum):
    AWAIT_ROLE_CHOICE = auto()
    AWAIT_ROLE_TYPE_CHOICE = auto()
    AWAIT_PHONE_NUMBER = auto()
    AWAIT_CODE = auto()
    AWAIT_TELEGRAM_PASSWORD = auto()
    AWAIT_BOT_PASSWORD = auto()
    AWAIT_BOT_PASSWORD_CONFIRMATION = auto()

class InitializationInfo(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    phone_num: str|None = None
    phone_code_hash: str|None = None
    tries_left: int = 5
    wait_task: asyncio.Task|None = None

class AppUserDBObject(BaseModel):
    user_id: int
    string_session: str
    set_read_after_summary: bool
    allow_all: bool
    preloading: bool
    process_images: bool = True
    history_size: int
    is_host: int
    order_by: str
    password_hash: str
    token_hash: str|None

class DialogDBObject(BaseModel):
    dialog_id: int
    user_id: int
    title: str
    is_allowed: bool
    channel_id: int|None
    topic_id: int|None

class DialogPriorityDBObject(BaseModel):
    dialog_id: int
    count: int

class UserGroupDBObject(BaseModel):
    group_id: int
    name: str
    password_hash: str
    created_by: int | None
    embedding_model: str | None = None

class MessageDBObject(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    message_id: int
    sender_id: int | None
    date_time: datetime
    text: str | None
    media_description: str | None
    embedding: Vector | ndarray | None
    # These columns are supplied by the search and summarization joins.
    title: str | None = None
    user_name: str | None = None

class SummaryTopic(BaseModel):
    start_message_id: int = Field(gt=0)
    end_message_id: int = Field(gt=0)
    topic: str
    summary: str

class PublicMessageDBObject(MessageDBObject):
    channel_id: int
    topic_id: int

class PrivateMessageDBObject(MessageDBObject):
    dialog_id: int
    user_id: int
    topic_id: int = 0

class AppUser(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: UserState
    client: TelegramClient|None = None
    dialogs: list[tuple[Dialog, ForumTopic|None]]|None = None
    chosen_ids: set[str]|None = None
    allowed_dialogs_options: set[str]|None = None
    last_read: tuple[Dialog, ForumTopic]|None = None
    allowed_dialogs: list[tuple[Dialog, ForumTopic]]|None = None
    cur_page: int = 0
    last_category: str|None = None
    category_dialogs_count: int = 0
    allowed_dialogs_set: set[tuple[int, int]]|None = None
    last_message: TelegramMessage | None = None  # Latest message with an inline keyboard.
    keep_last_message: bool = False  # Preserve input prompts after their buttons are retired.
    temporary_message: TelegramMessage | None = None  # Short notice removed on the next screen.
    pairing_code_hash: str|None = None
    pairing_code_expires_at: float = 0.0
    last_activity: float = Field(default_factory=monotonic)
    search_in_progress: bool = False
    search_since: datetime | None = None
    search_until: datetime | None = None
    pending_search_query: str | None = None

    # db
    user_id: int
    string_session: str|None=None
    set_read_after_summary: bool = False
    allow_all: bool = True
    preloading: bool = False
    process_images: bool = True
    history_size: int = 100
    is_host: int = False
    order_by: str = 'date'
    password_hash: str|None = None
    groups: list[UserGroupDBObject]|None = None
    pending_group_name: str | None = None
    pending_group_password_hash: str | None = None
    pending_group: UserGroupDBObject | None = None
    token_hash: str | None = None

class Host(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    user: AppUser
    requests_count: int = 0
    embedding_models: tuple[str, ...] = ()
    websocket: WebSocket
    request_lock: asyncio.Lock = Field(default_factory=asyncio.Lock)
    disconnected: bool = False

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Host):
            return NotImplemented
        return self.user.user_id == other.user.user_id

    def __lt__(self, other) -> bool:
        if not isinstance(other, Host):
            return NotImplemented
        return self.requests_count < other.requests_count

class Request(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_id: int
    host: Host
    future: asyncio.Future
    progress_queue: asyncio.Queue[tuple[int, int, str]] | None = None


class AppUserPreloading(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    idle_task: asyncio.Task[None]|None = None
    preloading: asyncio.Task[None]|None = None

class UninitializedUser(BaseModel):
    status: LoginStage = LoginStage.AWAIT_ROLE_CHOICE
    is_host: bool = False
    in_group: bool = False
    _init_info: InitializationInfo = PrivateAttr(default_factory=InitializationInfo)

    group_name: str|None = None
    hashed_password: str|None = None
    group: UserGroupDBObject | None = None

    @property
    def phone_num(self) -> str | None:
        return self._init_info.phone_num if self._init_info else None

    @property
    def phone_code_hash(self) -> str | None:
        return self._init_info.phone_code_hash if self._init_info else None

    @property
    def tries_left(self) -> int | None:
        timer = self._init_info.wait_task
        if timer is not None and timer.done() and not timer.cancelled():
            self._init_info.tries_left = 5
            self._init_info.wait_task = None
        return self._init_info.tries_left if self._init_info else None

    @phone_num.setter
    def phone_num(self, val: str | None) -> None:
        self._init_info.phone_num = val

    @phone_code_hash.setter
    def phone_code_hash(self, val: str | None) -> None:
        self._init_info.phone_code_hash = val

    @tries_left.setter
    def tries_left(self, val: int) -> None:
        if val <= 0:
            self._init_info.wait_task = asyncio.create_task(asyncio.sleep(300))
        else:
            self._init_info.wait_task = None
        self._init_info.tries_left = val

    @property
    def wait(self) -> bool:
        return not self._init_info.wait_task.done() if self._init_info and self._init_info.wait_task else False
