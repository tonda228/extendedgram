# Telegram Chat Assistant

A self-hosted Telegram bot that helps you quickly catch up on conversations and find information inside your Telegram chats.

The bot can:

- Summarize unread messages from your Telegram dialogs
- Search through messages using semantic / extended search
- Restrict access to only the chats you explicitly allow
- Work with private chats, groups, supergroups, channels, and forum topics
- Optionally mark messages as read after summarizing or searching
- Store message embeddings for semantic search
- Run locally with Docker

> This project uses your Telegram user session to read the chats you authorize. Keep your Telegram session string, API credentials, database credentials, and bot token private.

---

## Features

### Unread chat summaries

The bot can collect unread messages from allowed Telegram chats and generate a concise summary.

The summarization logic is designed to:

- Group related messages into topics
- Avoid mixing unrelated conversations
- Highlight important information, questions, decisions, plans, and conclusions

This makes it easier to catch up on active chats without reading every message manually.

---

### Extended chat search

The bot supports semantic search using embeddings.

Instead of searching only for exact words, you can search by meaning.

For example, a query such as:

```text
when did we discuss the database migration?
```

can match messages related to PostgreSQL migrations even if those exact words were not used.

The project stores message embeddings in PostgreSQL using `pgvector` and ranks results by vector similarity.

---

### Allowed chats

The bot does not need to search every Telegram dialog.

You can configure which dialogs are allowed.

Depending on your settings, the bot can:

- Allow all dialogs
- Restrict access to selected dialogs
- Treat forum topics separately
- Search or summarize only chats that are enabled for the current user

---

### Telegram forum topics

Forum-enabled supergroups are handled per topic.

This means that separate topics inside the same Telegram supergroup are represented independently instead of being treated as one large conversation.

---

### Optional read status changes

The bot can optionally mark messages as read after:

- Summarization
- Search

This behavior can be controlled through user settings.

---

## Technology stack

The project uses:

- Python
- Telethon
- python-telegram-bot
- PostgreSQL
- pgvector
- psycopg 3
- OpenAI-compatible chat completion API
- OpenAI-compatible embeddings API
- Docker
- Docker Compose

The LLM and embedding models can be hosted locally as long as they expose an OpenAI-compatible API.

---


# Requirements

Before starting, install:

- Docker
- Docker Compose

You will also need:

- A Telegram account
- Telegram API credentials
- A Telegram bot token
- PostgreSQL with `pgvector`
- A chat-completion model
- An embedding model

---

# Telegram credentials

## 1. Create Telegram API credentials

Create a Telegram application and obtain:

```text
API_ID
API_HASH
```

These credentials are used by Telethon.

More information you can find here:
https://core.telegram.org/api/obtaining_api_id

---

## 2. Create a Telegram bot

Create a bot using BotFather and obtain:

```text
BOT_TOKEN
```

The bot is the interface used to interact with the assistant.

Instructions can be found here:
https://core.telegram.org/bots/tutorial#getting-ready

---

# Environment variables

Create a `.env` file in the project root.

Example:

```env
# Telegram
API_ID=your_api_id
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token

# PostgreSQL
# Values of those variables you can change to whatever you want
DB_PASSWORD=your_password
DB_USER=your_user
DB_NAME=your_db_name
# Don't change DB_HOST
DB_HOST=db

# LLM
OPEN_AI_URL=http://model-runner.docker.internal/engines/v1/
COMPLETIONS_MODEL=docker.io/ai/qwen3-vl:8B
EMBEDDING_MODEL=ai/qwen3-embedding:0.6b
```

Add any additional variables required by your project.

Do not commit `.env` to Git.

---

# Running with Docker

The commands below assume your Docker Compose service containing the Telegram bot is named:

```text
bot
```

and that you run the commands from the directory containing `compose.yaml` or `docker-compose.yml`.

---

# First-time administrator authentication

Before using the bot, authenticate the Telegram account that will be used as the administrator.

Run:

```bash
docker compose build

docker compose up db -d

docker compose run --rm bot python telegram-bot/add_admin.py

docker compse up bot
```

The script should ask for:

```text
Enter your phone number:
```

Then Telegram will send you a login code.

Enter the code when requested.

If your Telegram account uses two-factor authentication, the script should also request your Telegram password.

Example flow:

```text
Enter your phone number: +420123456789
Enter the code you received: 12345
# And optionally
Enter your password: ********
```

After successful authentication, the Telethon session is saved to the database.

The `--rm` option automatically removes the temporary container after the script exits.

---

# Later

Just run

```bash
docker compose up -d
```

Or use GUI to start containers
If your PostgreSQL service has another name, replace `db` with that service name.

---