from telethon import TelegramClient, functions
from telethon.errors import PhoneCodeInvalidError, SessionPasswordNeededError, PasswordHashInvalidError
from telethon.sessions import StringSession
from telethon.tl.custom.message import Message
from telethon.tl.custom.dialog import Dialog
from telethon.tl.types import User
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

LOGGED_OUT = -1
WAIT_FOR_PHONE_NUMBER = 0
WAIT_FOR_CODE = 1
REQUIRES_PASSWORD = 2
AUTHENTICATED = 3
WAIT_FOR_SUMMARIZE_CHAT = 4
WAIT_FOR_READ = 5
WAIT_FOR_SEARCH_CHAT = 6
WAIT_FOR_SEARCH_TEXT = 7

user_info = {}
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

# add settings
# add preloading
# add event handlings

def initialize_db() -> None:
    cur.execute("""
    CREATE TABLE IF NOT EXISTS app_user (
        user_id BIGINT PRIMARY KEY,
        string_session TEXT NOT NULL,
        set_read_after_summary BOOLEAN NOT NULL,
        set_read_after_search BOOLEAN NOT NULL,
        allow_all BOOLEAN NOT NULL,
        allowed_chats BIGINT[],
        preloading BOOLEAN
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS media (
        media_id BIGSERIAL PRIMARY KEY,
        description TEXT NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS message (
        message_id BIGINT NOT NULL,
        chat_id BIGINT NOT NULL,
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
    CREATE INDEX ON message USING hnsw (embedding vector_cosine_ops)
    """)
    connection.commit()

def image_to_data_url(path: str | os.path) -> str:
    mime_type, _ = mimetypes.guess_file_type(path)
    with open(path, "rb") as file:
        encoded = base64.b64encode(file.read()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"

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


def validate_phone_number(number: str) -> bool:
    number = "".join(number.split())
    if len(number) != 13 or number[0] != '+' or not number[1:].isdigit():
        return False
    return True

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

# change or delete this

async def store_message_to_db(dialog: Dialog,
                              message: Message,
                              saved_messages,
                              saved_id: int,
                              sender_name: str,
                              add_embeddings = False) -> int:
    if saved_id < len(saved_messages) and saved_messages[saved_id].message_id == message.id:
        if add_embeddings and saved_messages[saved_id].embedding is None:
            await create_embedding(message, saved_messages[saved_id].media_description, True)
        return saved_id + 1

    media_description = None
    if message.media:
        if not os.path.exists("media"):
            os.makedirs("media")

        if message.photo:
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

    embedding = None if not add_embeddings else await create_embedding(message, media_description, False)

    sender = await message.get_sender()
    sender_name = "Sender name: "
    if dialog.is_channel:
        sender_name += sender.title
    else:
        if sender.username:
            sender_name += sender.username
        else:
            sender_name += sender.first_name + (sender.last_name if sender.last_name else "")

    cur.execute("""
                INSERT INTO message (message_id,
                                     chat_id,
                                     sender_id,
                                     sender_name,
                                     date_time,
                                     text,
                                     media_description,
                                     embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (message.id, dialog.id, sender.id, sender_name, message.date, message.text, media_description,
                      embedding))
    connection.commit()
    pass

async def store_unsaved_messages(dialog,
                                 last_id: int,
                                 saved_messages,
                                 client: TelegramClient,
                                 add_embeddings = False) -> None:
    saved_id = 0
    topic_id = dialog[1].id if dialog[1] else None
    async for message in client.iter_messages(dialog[0], reply_to=topic_id):
        if message.id <= last_id or message.date <= dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=100):
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


        embedding = None if not add_embeddings else await create_embedding(message, media_description, False)
        sender = await message.get_sender()
        sender_name = "Sender name: "
        if isinstance(sender, User):
            if sender.username:
                sender_name += sender.username
            else:
                sender_name += sender.first_name + (sender.last_name if sender.last_name else "")
        elif sender.is_channel:
            sender_name += sender.title

        topic = None
        if dialog[1]:
            topic = dialog[1].title


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
        """, (message.id, dialog[0].id, topic, sender.id, sender_name, message.date, message.text, media_description, embedding))
        connection.commit()


async def process_phone_number(update: Update, context, number):
    if not validate_phone_number(number):
        raise ValueError

    user_id = update.effective_user.id
    user_info[user_id]["phone_num"] = number

    client = user_info[user_id]["client"]
    await client.connect()
    result = await client.send_code_request(number)
    user_info[user_id]["phone_code_hash"] = result.phone_code_hash

    code_text = "Please enter your code:"
    await context.bot.send_message(chat_id=update.effective_chat.id, text=code_text)
    user_info[user_id]["status"] = WAIT_FOR_CODE

async def successful_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    client = user_info[user_id]["client"]

    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text="Successfully signed in.")
    cur.execute("""
                INSERT INTO app_user (user_id,
                                      string_session,
                                      set_read_after_summary,
                                      set_read_after_search,
                                      allow_all,
                                      allowed_chats)
                VALUES (%s, %s, %s, %s, %s, %s)
                """, (user_id, client.session.save(), False, False, True, []))
    connection.commit()
    user_info[user_id]["status"] = AUTHENTICATED

async def process_code(update: Update, context, code):
    code = "".join(code.split())
    user_id = update.effective_user.id
    client = user_info[user_id]["client"]
    number = user_info[user_id]["phone_num"]
    phone_code_hash = user_info[user_id]["phone_code_hash"]

    try:
        await client.sign_in(phone=number, code=code, phone_code_hash=phone_code_hash)
    except PhoneCodeInvalidError as e:
        user_info[user_id]["tries_left"] -= 1
        print(e)
        text = f"Could not login.\nYou have {user_info[user_id]["tries_left"]} left"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
        if user_info[user_id]["tries_left"] == 0:
            await client.disconnect()
    except SessionPasswordNeededError as e:
        print(e)
        user_info[user_id]["status"] = REQUIRES_PASSWORD
        text = "Two-steps verification is enabled and a password is required:"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    else:
        await successful_login(update, context)

async def process_password(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    user_id = update.effective_user.id
    client = user_info[user_id]["client"]

    try:
        await client.sign_in(password=text)
    except PasswordHashInvalidError as e:
        text = "Incorrect password. Try again:"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    else:
        await successful_login(update, context)

# async def process_qr_code(update: Update, context):
#     user_id = update.effective_user.id
#     client = user_info[user_id]["client"]
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
    user_id = update.effective_user.id
    status = user_info[user_id]["status"]
    dialog = user_info[user_id]["last_read"]
    client = user_info[user_id]["client"]
    query = update.callback_query

    await query.answer()
    await query.delete_message()

    if status == WAIT_FOR_READ:
        if query.data == "Yes":
            await client.send_read_acknowledge(dialog[0], clear_mentions=True, clear_reactions=True)
        await menu(update, context, query)
    if query.data == "summarize":
        await summarize(update, context)
    elif query.data == "search":
        await search(update, context)
    elif query.data == "settings":
        await query.message.reply_text("Settings are unavailable yet")


async def process_summarize_query(update: Update, context, text=""):
    if not update.effective_user:
        return
    user_id = update.effective_user.id
    client = user_info[user_id]["client"]
    unread_dialogs = user_info[user_id]["unread_dialogs"]
    dialog_id = text.split()[0]
    try:
        dialog_id = int(dialog_id)
    except ValueError:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid argument. Try again.")
        return
    if dialog_id == 0:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Thank you for your time.")
        user_info[user_id]["status"] = AUTHENTICATED
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
        await store_unsaved_messages(chosen_dialog, last_message_id, saved_messages, client, False)

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
    user_info[user_id]["status"] = WAIT_FOR_READ
    user_info[user_id]["last_read"] = chosen_dialog




async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_authentication(update, context):
        return

    user_id = update.effective_user.id
    client = user_info[user_id]["client"]

    dialogs = await client.get_dialogs()
    dialog_names = [dialog.name for dialog in dialogs]
    message = await context.bot.send_poll(question="Choose in which chats you want to search.",
                                options=dialog_names,
                                allows_multiple_answers=True,
                                is_anonymous=False,
                                chat_id=update.effective_chat.id)

    user_info[user_id]["status"] = WAIT_FOR_SEARCH_CHAT
    user_info[user_id]["given_dialogs"] = dialogs

    poll_messages[message.poll.id] = {
        "chat_id": message.chat_id,
        "message_id": message.message_id
    }

async def process_search_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    client = user_info[user_id]["client"]
    given_dialogs = user_info[user_id]["given_dialogs"]
    queried_dialogs = []
    if update.poll_answer and update.poll_answer.option_ids:
        queried_dialogs = [given_dialogs[index] for index in update.poll_answer.option_ids]

    user_info[user_id]["status"] = WAIT_FOR_SEARCH_TEXT
    user_info[user_id]["given_dialogs"] = queried_dialogs

    await context.bot.send_message(chat_id=poll_messages[update.poll_answer.poll_id]["chat_id"], text="Enter your query bellow:")

async def process_search_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    user_id = update.effective_user.id
    client = user_info[user_id]["client"]
    queried_dialogs = user_info[user_id]["given_dialogs"]

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

        # Leave only one db query

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
    user_id = update.effective_user.id
    if user_id in user_info:
        status = user_info[user_id]["status"]
        if status < AUTHENTICATED:
            user_info[user_id]["status"] = WAIT_FOR_PHONE_NUMBER
            phone_number_text = "To use this bot you need to be logged into your account.\nPlease enter your phone number below:"
            await context.bot.send_message(chat_id=update.effective_chat.id, text=phone_number_text)
            return False
        else:
            return True

    cur.execute("select * from app_user where user_id = %s", (user_id,))
    result = cur.fetchone()
    if result is None:
        user_info[user_id] = {
            "status": WAIT_FOR_PHONE_NUMBER,
            "client": TelegramClient(StringSession(), API_ID, API_HASH),
            "phone_num": None,
            "phone_code_hash": None,
            "tries_left": 5,
            "unread_dialogs": [],
            "given_dialogs": [],
            "last_read": None
        }

        phone_number_text = "To use this bot you need to be logged into your account.\nPlease enter your phone number below:"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=phone_number_text)
        return False
    else:
        session_string = result[1]
        client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
        await client.connect()
        user_info[user_id] = {
            "status": AUTHENTICATED,
            "client": client,
            "unread_dialogs": [],
            "given_dialogs": [],
            "last_read": None
        }
        return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_text = "Welcome to Busy Lazy Bot!\n"\
           "This bot will help you to find info you lost in your chats "\
           "and summarize your unread groups without the need to read them."
    await context.bot.send_message(chat_id=update.effective_chat.id, text=start_text)

    if await check_authentication(update, context):
        user_info[update.effective_user.id]["status"] = AUTHENTICATED

async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in user_info or user_info[update.effective_user.id]["status"] == LOGGED_OUT:
        return

    text = None
    if update.message:
        text = update.message.text

    status = user_info[update.effective_user.id]["status"]
    if status == WAIT_FOR_PHONE_NUMBER:
        await process_phone_number(update, context, text)
    elif status == WAIT_FOR_CODE:
        await process_code(update, context, text)
    elif status == REQUIRES_PASSWORD:
        await process_password(update, context, text)
    elif status == WAIT_FOR_SUMMARIZE_CHAT:
        await process_summarize_query(update, context, text)
    elif status == WAIT_FOR_SEARCH_CHAT:
        await process_search_chat(update, context)
    elif status == WAIT_FOR_SEARCH_TEXT:
        await process_search_text(update, context, text)


async def summarize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_authentication(update, context):
        return
    user_id = update.effective_user.id
    client = user_info[user_id]["client"]

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
                print(topic)
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

    user_info[user_id]["status"] = WAIT_FOR_SUMMARIZE_CHAT
    user_info[user_id]["unread_dialogs"] = unread_dialogs


async def main():
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    await asyncio.Event().wait()

if __name__ == "__main__":
    initialize_db()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("summarize", summarize))
    application.add_handler(CommandHandler("search", search))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(CallbackQueryHandler(process_button))
    application.add_handler(PollAnswerHandler(process_search_chat))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), process_message))

    asyncio.run(main())