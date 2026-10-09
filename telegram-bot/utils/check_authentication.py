import os

from dotenv import load_dotenv
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telethon import TelegramClient
from telethon.sessions import StringSession

from utils.classes import AppUser, UserState, UninitializedUser, LoginStage
from utils.helpers import send_message
from utils.state import user_info, uninitialized_users

load_dotenv()

# suggest to be host or user
# show categories of the choice
# initialize log in

# Telethon
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]

async def query_phone_number(user_id, is_user_in_memory: bool=False):
    phone_number_text = "To use this bot you need to be logged into your account.\nPlease enter your phone number below  (with + at the start):"
    if not is_user_in_memory:
        user_info[user_id] = AppUser(
            user_id,
            UserState.LOGGED_OUT,
            TelegramClient(StringSession(), API_ID, API_HASH)
        )
        app_user = user_info[user_id]
        uninitialized_user = uninitialized_users[user_id]
        app_user.is_host = uninitialized_user.is_host
    uninitialized_users[user_id].status = LoginStage.AWAIT_PHONE_NUMBER

    await send_message(chat_id=user_id, text=phone_number_text)

async def offer_role(user_id):
    text = ("First you need to choose your role.\n"
            "1️⃣ Host: if you want to host a local llm to process requests.\n"
            "Note that you would need to run program from github on your machine to create connection with server.\n"
            "2️⃣ User: you would need to join or create a group. Only active hosts from this group will be able to process your requests")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(text="Host", callback_data="host")],
        [InlineKeyboardButton(text="User", callback_data="user")]
    ])
    uninitialized_users[user_id].status = LoginStage.AWAIT_ROLE_CHOICE
    await send_message(user_id, text, keyboard)

async def offer_host_type(user_id):
    text = ("Choose what users can use your machine for computations.\n"
            "* Only me: in that case you won't be able to use this bot when your machine is turned off.\n"
            "* Group users: you can join or create your own group; "
            "when your llm is unavailable you can use hardware of other people in your group.\n")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(text="Only me", callback_data="solo")],
        [InlineKeyboardButton(text="Group users", callback_data="group")]
    ])
    uninitialized_users[user_id].status = LoginStage.AWAIT_ROLE_TYPE_CHOICE
    await send_message(user_id, text,keyboard)

async def handle_host_type(user_id, user):
    if user.role_type == "solo":
        await query_phone_number(user_id)
    elif user.role_type == "group":
        await query_phone_number(user_id)
        # await offer_group_options(user_id)
    elif user.role_type == "all":
        # add ways to verify host
        await query_phone_number(user_id)

async def offer_group_options(user_id):
    text = "Choose your next action."
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(text="Create group", callback_data="create")],
        [InlineKeyboardButton(text="Join group", callback_data="join")]
    ])
    uninitialized_users[user_id].status = LoginStage.AWAIT_GROUP_CHOICE
    await send_message(chat_id=user_id, text=text, reply_markup=keyboard)

# Use this in later versions
async def offer_available_hosts(user_id):
    text = ("Choose what type of hosts will be used to process your messages."
            "* Verified: this is recommended level; hosts from this category are verified to run exactly the provided code "
            "so they won't be able to access your account in any way."
            "* Checked: hosts from this category are checked but not guarantied to run provided code.")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(text="Verified", callback_data="verified")],
        [InlineKeyboardButton(text="Checked", callback_data="checked")]
    ])
    uninitialized_users[user_id].status = LoginStage.AWAIT_GROUP_CHOICE
    await send_message(chat_id=user_id, text=text, reply_markup=keyboard)

async def offer_user_type(user_id):
    text = ("Choose what type of user you want to be.\n"
            "1️⃣ Plain user: you will use available machine for needed computations if there is any.\n"
            "2️⃣ Group user: you will need to join or create a group")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(text="Plain user", callback_data="plain")],
        [InlineKeyboardButton(text="Group user", callback_data="group")]
    ])
    uninitialized_users[user_id].status = LoginStage.AWAIT_ROLE_TYPE_CHOICE
    await send_message(chat_id=user_id, text=text, reply_markup=keyboard)

async def check_authentication(user_id) -> bool:
    is_user_in_memory = user_id in user_info
    if is_user_in_memory and user_info[user_id].status >= UserState.AUTHENTICATED:
        return True
    elif is_user_in_memory and user_info[user_id].status < UserState.AUTHENTICATED:
        await query_phone_number(user_id, is_user_in_memory)
    elif user_id not in uninitialized_users:
        uninitialized_users[user_id] = UninitializedUser()
        await offer_role(user_id)
    return False