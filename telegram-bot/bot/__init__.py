import os

from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder


load_dotenv()
# Telegram BOT
BOT_API_TOKEN = os.environ["BOT_API_TOKEN"]
application = ApplicationBuilder().token(BOT_API_TOKEN).concurrent_updates(True).build()