from telethon import TelegramClient, functions
from telethon.errors import PhoneCodeInvalidError, SessionPasswordNeededError, PasswordHashInvalidError
from telethon.sessions import StringSession
from telethon.tl.custom.message import Message
from telethon.tl.custom.dialog import Dialog
from telethon.client import telegramclient
from telethon.tl.types import User, Channel
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import filters, ApplicationBuilder, CommandHandler, MessageHandler, PollAnswerHandler, ContextTypes, CallbackQueryHandler
import os
import asyncio
import mimetypes
import base64
import json
import psycopg
from psycopg.rows import namedtuple_row, dict_row
from pgvector.psycopg import register_vector
from pgvector import Vector
import httpx
from openai import AsyncOpenAI
import datetime as dt
from enum import IntEnum, auto

class UserState(IntEnum):
    LOGGED_OUT = auto()
    WAIT_FOR_PHONE_NUMBER = auto()
    WAIT_FOR_CODE = auto()
    WAIT_FOR_PASSWORD = auto()
    AUTHENTICATED = auto()
    WAIT_FOR_SUMMARIZE_CHAT = auto()
    WAIT_FOR_READ = auto()
    WAIT_FOR_SEARCH_CHAT = auto()
    WAIT_FOR_SEARCH_TEXT = auto()
    WAIT_FOR_LOG_OUT_CONFIRMATION = auto()

class AppUser:
    def __init__(self, status: UserState, client: telegramclient.TelegramClient, app_user=None):
        self.status = status
        self.client = client
        self.dialogs = None
        self.last_read = None
        self.phone_num = None
        self.phone_code_hash = None
        self.tries_left = 5

        self.preloading = False if not app_user else app_user.preloading
        self.history_size = 0 if not app_user else app_user.history_size
        self.set_read_after_summary = False if not app_user else app_user.history_size
        self.allow_all = True if not app_user else app_user.allow_all

class AppUserPreloading:
    def __init__(self):
        self.idle_task = None
        self.preloading = None

user_info: dict[int, AppUser] = {}
poll_messages = {}

# Telethon
load_dotenv()
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]

# Telegram BOT
BOT_API_TOKEN = os.environ["BOT_API_TOKEN"]
application = ApplicationBuilder().token(BOT_API_TOKEN).concurrent_updates(True).build()

# Completions model API
URL = "http://localhost:12434/engines/v1/chat/completions"
COMPLETIONS_MODEL = "docker.io/ai/qwen3-vl:8B"
requests_client = httpx.AsyncClient(timeout=None)

# OpenAI API
OPEN_AI_URL = "http://localhost:12434/engines/v1/"
completions_client = AsyncOpenAI(base_url=OPEN_AI_URL)

# Initializing database
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_HOST = os.environ["DB_HOST"]
connection = psycopg.connect(dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=5432, row_factory=namedtuple_row)
connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
register_vector(connection)
cur = connection.cursor()

# Embeddings model API
EMBEDDING_MODEL = "ai/qwen3-embedding:0.6b"
EMBEDDING_URL = "http://localhost:12434/engines/v1/embeddings"

# add preloading
# add event handlings
# add classes for data encapsulation

def initialize_db() -> None:
    cur.execute("""
    CREATE TABLE IF NOT EXISTS app_user (
        user_id BIGINT PRIMARY KEY,
        string_session TEXT NOT NULL,
        set_read_after_summary BOOLEAN NOT NULL,
        set_read_after_search BOOLEAN NOT NULL,
        allow_all BOOLEAN NOT NULL,
        preloading BOOLEAN NOT NULL,
        hisotry_size BIGINT NOT NULL,
        allowed_chats BIGINT[]
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS message (
        message_id BIGINT,
        chat_id BIGINT,
        topic TEXT,
        sender_id BIGINT NOT NULL,
        sender_name TEXT NOT NULL,
        date_time TIMESTAMPTZ NOT NULL,
        text TEXT,
        media_description TEXT,
        embedding vector(1024),
        PRIMARY KEY (chat_id, message_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS app_user_message (
        user_id BIGINT REFERENCES app_user(user_id) ON DELETE CASCADE,
        message_id BIGINT,
        chat_id BIGINT,
        PRIMARY KEY (user_id, message_id, chat_id),
        FOREIGN KEY (message_id, chat_id)
            REFERENCES message(message_id, chat_id)
            ON DELETE CASCADE
    )
    """)

    cur.execute("""
    CREATE INDEX ON message USING hnsw (embedding vector_cosine_ops)
    """)
    connection.commit()

async def create_embedding(message: Message, media_description: str | None, update: bool = False) -> Vector:
    text = "Text: " + ("None" if message.text is None else message.text)
    media = "Media: " + ("None" if media_description is None else media_description)
    full_text = text + "\n" + media
    headers = {
        "Content-Type": "application/json"
    }
    payload = {
        "model": EMBEDDING_MODEL,
        "input": full_text
    }
    response = await requests_client.post(EMBEDDING_URL, headers=headers, json=payload)
    embedding = Vector(response.json()["data"][0]["embedding"])

    if update:
        cur.execute("""
        UPDATE message
        SET embedding = %s
        WHERE message_id = %s
        """, (embedding, message.id))
        connection.commit()
    return embedding

def image_to_data_url(path: str | os.path) -> str:
    mime_type, _ = mimetypes.guess_file_type(path)
    with open(path, "rb") as file:
        encoded = base64.b64encode(file.read()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"

async def translate_image(message: Message):
    media = await message.download_media("media")
    request_data = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "Give very concise one sentence description of this image"
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": image_to_data_url(media)
                }
            }
        ]
    }
    response = await completions_client.chat.completions.create(
        model=COMPLETIONS_MODEL,
        messages=[request_data])
    media_description = response.choices[0].message.content
    os.remove(media)
    return media_description

async def store_unsaved_messages(user_id: int,
                                 dialog,
                                 last_id: int,
                                 saved_messages,
                                 client: TelegramClient,
                                 add_embeddings = False) -> None:
    saved_id = 0
    topic_id = dialog[1].id if dialog[1] else None
    limit = dialog[1].unread_count if dialog[1] else dialog[0].unread_count
    days = user_info[user_id].history_size
    if days == 0:
        days = 42
    if not add_embeddings:
        limit = None

    async for message in client.iter_messages(dialog[0], reply_to=topic_id, limit=limit):
        if message.id <= last_id or message.date <= dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=days):
            break

        if saved_id < len(saved_messages) and saved_messages[saved_id].message_id == message.id:
            if add_embeddings and saved_messages[saved_id].embedding is None:
                await create_embedding(message, saved_messages[saved_id].media_description, True)
            saved_id += 1
            continue

        media_description = None
        if message.media:
            if not os.path.exists("media"):
                os.makedirs("media")

            if message.photo:
                media_description = await translate_image(message)


        embedding = None if not add_embeddings else await create_embedding(message, media_description, False)
        sender = await message.get_sender()
        sender_name = "Sender name: "
        if isinstance(sender, User):
            if sender.username:
                sender_name += sender.username
            else:
                sender_name += sender.first_name + (sender.last_name if sender.last_name else "")
        elif isinstance(sender, Channel):
            sender_name += sender.title

        topic = None
        if dialog[1]:
            topic = dialog[1].title
        sender_id = sender.id if sender else dialog[0].id

        cur.execute("""
        INSERT INTO message (
            message_id,
            chat_id,
            topic,
            sender_id,
            sender_name,
            date_time,
            text,
            media_description,
            embedding
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (message.id, dialog[0].id, topic, sender_id, sender_name, message.date, message.text, media_description, embedding))

        cur.execute("""
        INSERT INTO app_user_message (
            user_id,
            message_id,
            chat_id
        ) VALUES (%s, %s, %s)
        """, (user_id, message.id, dialog[0].id))
        connection.commit()


def validate_phone_number(number: str) -> bool:
    number = "".join(number.split())
    if len(number) != 13 or number[0] != '+' or not number[1:].isdigit():
        return False
    return True

async def process_phone_number(update: Update, context, number):
    if not validate_phone_number(number):
        raise ValueError

    user_id = update.effective_user.id
    user_info[user_id].phone_num = number

    client = user_info[user_id].client
    await client.connect()
    result = await client.send_code_request(number)
    user_info[user_id].phone_code_hash = result.phone_code_hash

    code_text = "Please enter your code:"
    await context.bot.send_message(chat_id=update.effective_chat.id, text=code_text)
    user_info[user_id].status = UserState.WAIT_FOR_CODE

async def process_code(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str):
    code = "".join(code.split())
    user_id = update.effective_user.id
    app_user = user_info[user_id]
    client = user_info[user_id].client
    number = user_info[user_id].phone_num
    phone_code_hash = user_info[user_id].phone_code_hash

    if app_user.tries_left <= 0:
        # change this later
        await context.bot.send_message(chat_id=update.effective_chat.id, text="You have no tries left. Wait for 5 minutes to retry.")
        return

    try:
        await client.sign_in(phone=number, code=code, phone_code_hash=phone_code_hash)
    except PhoneCodeInvalidError as e:
        app_user.tries_left -= 1
        print(e)
        text = f"Could not login.\nYou have {app_user.tries_left} left"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
        if app_user.tries_left == 0:
            client.disconnect()
    except SessionPasswordNeededError as e:
        print(e)
        app_user.tries_left = UserState.WAIT_FOR_PASSWORD
        text = "Two-steps verification is enabled and a password is required:"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    else:
        await successful_login(update, context)

async def process_password(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    user_id = update.effective_user.id
    client = user_info[user_id].client

    try:
        await client.sign_in(password=text)
    except PasswordHashInvalidError as e:
        text = "Incorrect password. Try again:"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    else:
        await successful_login(update, context)

async def successful_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    client = user_info[user_id].client

    await context.bot.send_message(chat_id=update.effective_chat.id, text="Successfully signed in.")
    cur.execute("""
    INSERT INTO app_user (user_id,
                          string_session,
                          set_read_after_summary,
                          set_read_after_search,
                          allow_all,
                          allowed_dialogs)
    VALUES (%s, %s, %s, %s, %s, %s)
    """, (user_id, client.session.save(), False, False, True, []))
    connection.commit()
    user_info[user_id].status = UserState.AUTHENTICATED

# async def process_qr_code(update: Update, context):
#     user_id = update.effective_user.id
#     client = user_info[user_id].client
#     qr_login = await client.qr_login()
#
#     print(qr_login.url)
#
#     await qr_login.wait()

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE, send_with = None):
    if not await check_authentication(update, context):
        return

    keyboard = [
        [
            InlineKeyboardButton(text="Summarize", callback_data="summarize"),
            InlineKeyboardButton(text="Search", callback_data="search"),
            InlineKeyboardButton(text="Settings", callback_data="settings")
        ]
    ]
    send_with = send_with if send_with else update
    await send_with.message.reply_text("Where do you wish to continue?", reply_markup=InlineKeyboardMarkup(keyboard))

async def process_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_authentication(update, context):
        return

    user_id = update.effective_user.id
    app_user = user_info[user_id]
    status = app_user.status
    dialog = app_user.last_read
    client = app_user.client
    query = update.callback_query

    reset_idle_timer(user_id)

    await query.answer()
    await query.delete_message()

    if status == UserState.WAIT_FOR_READ:
        if query.data == "Yes":
            await client.send_read_acknowledge(dialog[0], clear_mentions=True, clear_reactions=True)
        await menu(update, context, query)
        user_info[user_id].status = UserState.AUTHENTICATED
    elif query.data == "summarize":
        await summarize_request(update, context)
    elif query.data == "search":
        await search_request(update, context)
    elif query.data == "settings":
        await settings(update, context)
    elif query.data in ["allow_all", "set_read_after_summary", "preloading"]:
        column = query.data
        cur.execute(f"""
        UPDATE app_user
        SET {column} = NOT {column}
        WHERE user_id = %s
        """, (user_id,))
        connection.commit()

        current_value = getattr(user_info[user_id], column)
        setattr(user_info[user_id], column, not current_value)
        print(app_user.preloading)
        await settings(update, context)
    elif query.data == "allowed_dialogs":
        cur.execute("""
        SELECT allowed_dialogs
        FROM app_user
        WHERE user_id = %s
        """, (update.effective_user.id,))
        await settings(update, context)
    elif query.data == "back":
        pass


async def get_message_info(data, message):
    # adding info about sender
    name = "Username: " + message.sender_name
    data.append({
        "type": "text",
        "text": name
    })

    message_id = str(message.message_id)
    date =  message.date_time.strftime("%H:%M:%S %d.%m.%Y")
    message_text = "Message " + message_id + " " + date + ": " + (message.text if message.text else "")
    media_text = "Media: " + ("None" if message.media_description is None else message.media_description)
    full_text = message_text + "\n" + media_text
    # adding info about message
    data.append({
        "type": "text",
        "text": full_text
    })

async def summarize_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_authentication(update, context):
        return
    user_id = update.effective_user.id
    client = user_info[user_id].client

    unread_dialogs = []
    index = 1
    async for dialog in client.iter_dialogs():
        if dialog.unread_count == 0:
            continue
        if dialog.is_group and getattr(dialog.entity, "forum", False):
            result = await client(
                functions.messages.GetForumTopicsRequest(
                    peer=dialog,
                    offset_date=None,
                    offset_id=0,
                    offset_topic=0,
                    limit=100
                )
            )
            for topic in result.topics:
                if topic.unread_count == 0:
                    continue
                chat_name = f"{dialog.name}|{topic.title}"
                await context.bot.send_message(chat_id=update.effective_chat.id, text=
                    f"{index}) In {chat_name}: {topic.unread_count} unread message{"" if topic.unread_count == 1 else "s"}")
                unread_dialogs.append((dialog, topic))
                index += 1
            continue
        await context.bot.send_message(chat_id=update.effective_chat.id, text=
            f"{index}) In {dialog.name}: {dialog.unread_count} unread message{"" if dialog.unread_count == 1 else "s"}")
        unread_dialogs.append((dialog, None))
        index += 1

    await context.bot.send_message(chat_id=update.effective_chat.id, text="Choose which chat you want to summarize, "
          f"that is a number from 0 to {len(unread_dialogs)}, where 0 means none.")

    user_info[user_id].status = UserState.WAIT_FOR_SUMMARIZE_CHAT
    user_info[user_id].dialogs = unread_dialogs

async def process_summarize_query(update: Update, context, text=""):
    if not update.effective_user:
        return
    user_id = update.effective_user.id
    client = user_info[user_id].client
    unread_dialogs = user_info[user_id].dialogs
    dialog_id = text.split()[0]
    try:
        dialog_id = int(dialog_id)
    except ValueError:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid argument. Try again.")
        return
    if dialog_id == 0:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Thank you for your time.")
        user_info[user_id].status = UserState.AUTHENTICATED
        return
    if dialog_id < 0 or dialog_id > len(unread_dialogs):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Your choice is out of range. Try again.")
        return

    chosen_dialog = unread_dialogs[dialog_id - 1]
    data = []
    messages_count = chosen_dialog[0].unread_count if not chosen_dialog[1] else chosen_dialog[1].unread_count
    cur.execute("""
    select *
    from message
    where chat_id = %s
    order by message_id desc
    limit %s
    """, (chosen_dialog[0].id, messages_count))
    saved_messages = cur.fetchall()
    last_message_id = saved_messages[0].message_id if saved_messages else 0
    if saved_messages is None:
        saved_messages = []

    if last_message_id != chosen_dialog[0].message.id:
        await store_unsaved_messages(user_id, chosen_dialog, last_message_id, saved_messages, client, False)

    cur.execute("""
    select * from (select *
                   from message
                   where chat_id = %s
                   order by message_id desc
                       limit %s)
    order by message_id asc
    """, (chosen_dialog[0].id, messages_count))
    messages = cur.fetchall()
    for message in messages:
        await get_message_info(data, message)

    response_format = ("["
                       "     {"
                       "         start_message_id: ...,"
                       "         end_message_id: ...,"
                       "         topic: ...,"
                       "         summary: ..."
                       "     }"    
                       "]")
    request_data = {
        "model": "docker.io/ai/qwen3-vl:8B",
        "messages": [
            {
                "role": "system",
                "content":  "Summarize the messages very concisely. "
                            "For each message, you first receive the sender name and then the message. "
                            "Group related messages into topics, but do not merge unrelated conversations. "
                            "For each topic, include the first and last message IDs. "
                            "Return at most 20 topics. "
                            "Prioritize only important information, decisions, questions, plans, and conclusions. "
                            "Ignore greetings, repetition, jokes, filler, and minor details unless they are necessary to understand the topic. "
                            "For every 20 input messages, produce approximately 1 topic summary when possible. "
                            "Each topic summary should normally be 1-3 sentences and no more than 60 words. "
                            "Use a surface-level summary only: do not retell the conversation message by message. "
                            "Do not include examples, background explanations, or details that are not essential. "
                            "If several messages repeat the same idea, mention it only once. "
                            "If the conversation is short or contains little important information, return fewer topics rather than adding detail. "
                            "Add information about who says what if that person talks about his situation "
                            f"Response give in json in following format:\n {response_format}"

            },
            {
                "role": "user",
                "content": data
            }
        ]
    }
    results = []
    while True:
        try:
            response = await requests_client.post(URL, json=request_data)
            results = json.loads(response.json()["choices"][0]["message"]["content"])
        except json.decoder.JSONDecodeError:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Error occurred. Retrying...")
        else:
            break

    for index, result in enumerate(results, start=1):
        messages = ""
        if chosen_dialog[0].is_channel:
            url_start = await client(functions.channels.ExportMessageLinkRequest(
                channel=chosen_dialog[0],
                id=result["start_message_id"]
            ))
            url_end = await client(functions.channels.ExportMessageLinkRequest(
                channel=chosen_dialog[0],
                id=result["end_message_id"]
            ))
            link_start = f"<a href='{url_start.link}'>here</a>"
            link_end = f"<a href='{url_end.link}'>here</a>"
            messages = f"From {link_start} to {link_end}\n"
        await update.message.reply_html(text=f"{index}) {result["topic"]}\n"
                                             f"{messages}{result["summary"]}",
                                        disable_web_page_preview=True)

    keyboard = [
        [
            InlineKeyboardButton(text="Yes", callback_data="Yes"),
            InlineKeyboardButton(text="No", callback_data="No")
        ]
    ]

    await update.message.reply_text(text="Mark chat as read?", reply_markup=InlineKeyboardMarkup(keyboard))
    user_info[user_id].status = UserState.WAIT_FOR_READ
    user_info[user_id].last_read = chosen_dialog


async def search_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_authentication(update, context):
        return

    user_id = update.effective_user.id
    app_user = user_info[user_id]
    client = user_info[user_id].client

    dialogs = await client.get_dialogs()
    dialog_names = [dialog.name for dialog in dialogs]

    dialogs = app_user.allowed_dialogs if not app_user.allow_all else await client.get_dialogs()

    text = "Choose in which chats you want to search. Separate chats indexes by coma. All whitespaces will be ignored."
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    dialogs_text = ""
    for index, dialog in enumerate(dialogs, start=1):
        if index != 1:
            dialogs_text += '\n'
        dialogs_text += f"{index}) {dialog.name}"

    await context.bot.send_message(chat_id=update.effective_chat.id, text=dialogs_text)
    user_info[user_id].status = UserState.WAIT_FOR_SEARCH_CHAT
    user_info[user_id].dialogs = dialogs

async def process_search_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, text) -> None:
    try:
        indexes = [int(index) for index in text.split(",")]
    except ValueError:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid input. Try again:")
        return

    user_id = update.effective_user.id

    given_dialogs = user_info[user_id].dialogs
    queried_dialogs = [given_dialogs[index] for index in indexes]

    user_info[user_id].status = UserState.WAIT_FOR_SEARCH_TEXT
    user_info[user_id].dialogs = queried_dialogs

    await context.bot.send_message(chat_id=poll_messages[update.poll_answer.poll_id]["chat_id"], text="Enter your query bellow:")

async def process_search_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    user_id = update.effective_user.id
    client = user_info[user_id].client
    queried_dialogs = user_info[user_id].dialogs

    headers = {
        "Content-Type": "application/json"
    }
    payload = {
        "model": EMBEDDING_MODEL,
        "input": text
    }
    response = await requests_client.post(EMBEDDING_URL, headers=headers, json=payload)
    embedding = Vector(response.json()["data"][0]["embedding"])

    best_messages = []
    for dialog in queried_dialogs:
        cur.execute("""
        select *
        from message
        where chat_id = %s
        order by message_id desc
        """, (dialog.id, ))
        saved_messages = cur.fetchall()
        await store_unsaved_messages(dialog, 0, saved_messages, client, True)

        cur.execute("""
        SELECT *
        FROM message
        WHERE chat_id = %s
        ORDER BY embedding <=> %s
        LIMIT 10
        """, (dialog.id, embedding))
        best_messages += cur.fetchall()

    data = []
    for message in best_messages:
        await get_message_info(data, message)

    request_data = {
        "model": "docker.io/ai/qwen3-vl:8B",
        "messages": [
            {
                "role": "system",
                "content": "Give short answer on the following question: " + text
            },
            {
                "role": "user",
                "content": data
            }
        ]
    }
    response = await requests_client.post(URL, json=request_data)
    result = response.json()["choices"][0]["message"]["content"]
    await context.bot.send_message(chat_id=update.effective_chat.id, text=result, parse_mode="Markdown")



async def check_authentication(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    phone_number_text = "To use this bot you need to be logged into your account.\nPlease enter your phone number below:"
    user_id = update.effective_user.id

    if user_id in user_info and user_info[user_id].status < UserState.AUTHENTICATED:
        user_info[user_id].status = UserState.WAIT_FOR_PHONE_NUMBER
        await context.bot.send_message(chat_id=update.effective_chat.id, text=phone_number_text)
    elif user_id not in user_info:
        user_info[user_id] = AppUser(
            UserState.WAIT_FOR_PHONE_NUMBER,
            TelegramClient(StringSession(), API_ID, API_HASH)
        )
        await context.bot.send_message(chat_id=update.effective_chat.id, text=phone_number_text)
    return user_info[user_id].status >= UserState.AUTHENTICATED

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_text = "Welcome to Busy Lazy Bot!\n"\
           "This bot will help you to find info you lost in your chats "\
           "and summarize your unread groups without the need to read them."
    await context.bot.send_message(chat_id=update.effective_chat.id, text=start_text)

    reset_idle_timer(update.effective_user.id)

    if await check_authentication(update, context):
        user_info[update.effective_user.id].status = UserState.AUTHENTICATED

async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in user_info or user_info[update.effective_user.id].status == UserState.LOGGED_OUT:
        return

    text = None
    if update.message:
        text = update.message.text

    status = user_info[update.effective_user.id].status
    if status == UserState.WAIT_FOR_PHONE_NUMBER:
        await process_phone_number(update, context, text)
    elif status == UserState.WAIT_FOR_CODE:
        await process_code(update, context, text)
    elif status == UserState.WAIT_FOR_PASSWORD:
        await process_password(update, context, text)
    elif status == UserState.WAIT_FOR_SUMMARIZE_CHAT:
        await process_summarize_query(update, context, text)
    elif status == UserState.WAIT_FOR_SEARCH_CHAT:
        await process_search_chat(update, context)
    elif status == UserState.WAIT_FOR_SEARCH_TEXT:
        await process_search_text(update, context, text)

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_authentication(update, context):
        return
    user_id = update.effective_user.id
    app_user = user_info[user_id]
    allow_all = "ON" if app_user.allow_all else "OFF"
    set_read_after_summary = "ON" if app_user.set_read_after_summary else "OFF"
    preloading = "ON" if app_user.preloading else "OFF"
    history_size = app_user.history_size
    keyboard = [
        [
            InlineKeyboardButton(text="Allowed dialogs", callback_data="allowed_dialogs"),
            InlineKeyboardButton(text=f"Allow all: {allow_all}", callback_data="allow_all")
        ],
        [
            InlineKeyboardButton(text=f"Read after summary: {set_read_after_summary}", callback_data="set_read_after_summary"),
            InlineKeyboardButton(text=f"Preloading: {preloading}", callback_data="preloading")
        ],
        [
            InlineKeyboardButton(text=f"History size: {history_size} days", callback_data="history_size")
        ],
        [
            InlineKeyboardButton(text="Back", callback_data="back")
        ]
    ]
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Settings", reply_markup=InlineKeyboardMarkup(keyboard))

async def log_out_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton(text="Yes, I do.", callback_data=True),
            InlineKeyboardButton(text="No, I do not.", callback_data=False)
        ]
    ]
    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text="Do you wish do log out and delete all your data?",
                                   reply_markup=InlineKeyboardMarkup(keyboard))

async def log_out_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur.execute("""
    DELETE
    FROM app_user
    WHERE user_id = %s
    """, (update.effective_user.id,))

    cur.execute("""
    DELETE
    FROM message m
    WHERE NOT EXISTS (SELECT 1
                      FROM app_user_message aum
                      WHERE aum.message_id = m.message_id
                        AND aum.chat_id != m.chat_id)
    """)
    connection.commit()

    if update.effective_user.id in user_info:
        del user_info[update.effective_user.id]
    await context.bot.send_message(chat_id=update.effective_chat.id, text="You've successfully logged out.")

async def main():
    await initialize_users()
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    await asyncio.Event().wait()

if __name__ == "__main__":
    initialize_db()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("summarize", summarize_request))
    application.add_handler(CommandHandler("search", search_request))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(CommandHandler("settings", settings))
    application.add_handler(CommandHandler("log_out", log_out_request))
    application.add_handler(CallbackQueryHandler(process_button))
    application.add_handler(PollAnswerHandler(process_search_chat))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), process_message))

    asyncio.run(main())