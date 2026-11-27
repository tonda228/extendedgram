# ExtendedGram

**Catch up on Telegram conversations, find forgotten messages, and stay organized with AI.**

ExtendedGram is a Telegram assistant for people who have too many messages to read and too much information scattered across chats. It can summarize conversations, search for messages by meaning, and help you focus on the chats that matter to you—all through a Telegram bot.

It works with private chats, groups, channels, and individual forum topics. You decide which conversations the assistant is allowed to process. **Your Telegram session is encrypted before it is stored; encryption is mandatory.**

> **Project status:** ExtendedGram is under active development. Availability and the exact menus may change.

## What can you do with ExtendedGram?

### 📝 Catch up without scrolling

If a group has hundreds of unread messages, you can ask ExtendedGram to summarize the conversation instead of reading everything one by one.

The assistant groups messages into related topics and focuses on useful details such as **decisions, deadlines, questions, plans, and conclusions**, rather than repeating the entire chat.

**For example:** After missing a day of messages in a university group, you could get a summary of upcoming assignments, changes to a meeting time, and questions that still need answers.

You can adjust how much conversation history the assistant takes into account. If you prefer, you can also have messages marked as read after a summary.

### 🔎 Find messages by meaning

Telegram's ordinary search is useful when you remember a word or phrase. ExtendedGram is designed for the times when you remember **what was discussed**, but not **how it was phrased**.

For example, search for:

> What did we agree on for the trip next weekend?

ExtendedGram can look for related conversations about dates, transport, or accommodation—even if your exact question never appeared in the chat. It also retrieves nearby messages so you can see the surrounding context.

Search quality depends on the available message history and the AI model; relevant results are not guaranteed every time.

### 💬 Keep different conversations separate

ExtendedGram supports:

- Private one-to-one chats
- Groups and supergroups
- Channels
- Individual forum topics inside groups

Forum topics are handled separately so that, for example, a group's *Homework* topic does not get mixed up with its *Announcements* topic.

### ⚙️ Control how it behaves

You can personalize the assistant through its settings:

- **Allowed conversations:** Let it process all available chats or only chats you select.
- **History size:** Choose how much message history is considered.
- **Read status:** Decide whether processed messages should be marked as read automatically.
- **Preloading:** Configure preparation of message history for later searches.
- **Process new images:** Choose whether the bot should generate descriptions for
  images without a saved description. Saved descriptions are reused either way.

This lets you use ExtendedGram for a single busy group or across multiple conversations.

## Getting started

You don't need to understand databases, AI models, or servers to use the bot.

1. **Open an ExtendedGram bot instance you trust.** Get the bot's Telegram link from its operator. There is no public bot link listed here yet.
2. **Send `/start`.** Follow the instructions shown by the bot.
3. **Connect your Telegram account.** Enter your phone number and Telegram verification code. If two-step verification is enabled, follow the additional password prompt.
4. **If you chose to be a host**, configure `host/.env` and run the host from the repository root after you receive the pairing code:

You can download only the host folder using Git sparse checkout. See the
[host-only setup instructions](host/README.md) for downloading, configuration,
running, and updating without checking out the bot's source code.

```dotenv
COORDINATOR_URL=https://YOUR_VPS_IP
OPEN_AI_URL=http://127.0.0.1:1234/v1
OPENAI_API_KEY=replace-with-your-model-server-key
COMPLETIONS_MODEL=your-completions-model
EMBEDDING_MODEL=your-embedding-model
```

Use the URL and model names from your model server. If that server explicitly
allows unauthenticated requests, set `OPENAI_API_KEY=local`; the client still
requires a nonempty value. For an authenticated endpoint, use its real API key.

```commandline
python -m pip install -r host/requirements.txt
python -m host.main
```
Enter your user ID, pairing code, and bot password in the host terminal. You
can use the bot without a connected host and request a fresh code under
**Settings → Pair host**. Codes expire after 10 minutes. AI operations still
need a compatible connected host.
Press Ctrl+C in the host terminal to stop it. Pending local tasks are cancelled
and the WebSocket is closed before exit; an already-closed connection does not
interrupt cleanup. Terminal interruption is reported without a traceback, and
saved pairing credentials are retained for the next launch.
Hosts declare which embedding models they support when connecting. A shared
host is eligible for a group only if it offers the group's exact model name.
All models must produce 1,024-dimensional embeddings and be available on the
host's model server. Restart both processes after updating their code.

Bot sessions expire after 30 minutes without button presses or messages when
the user's own host is disconnected. Disconnecting that host starts a fresh
30-minute period; a connected own host keeps the session open. Expiry closes
the Telegram client and asks for the bot password, while retaining stored data.
Background preloading does not extend the period. A foreground search or summary
can finish before the idle period starts.

The bot removes completed selection screens and replaces old menus when opening
a new screen. Download and model progress messages are removed after completion
or failure. Login and host-connection confirmations, search answers, and summaries
remain in the chat. Only explicitly temporary form prompts are removed on the next
screen. Processing errors are displayed
with the menu buttons instead of adding a separate error and menu message.

On a host, keep `EMBEDDING_MODEL` for one model, or set an ordered list in
`host/.env` for multiple models:

```dotenv
EMBEDDING_MODELS=model-a,model-b
```

When `EMBEDDING_MODELS` is set, it takes precedence over the host's
`EMBEDDING_MODEL`. The first model is used for your own requests on your host;
shared group requests use the model required by the group. The coordinator
still uses its singular `EMBEDDING_MODEL` as the default for groups.

The coordinator has its own dependencies in `telegram-bot/requirements.txt`.
For separate local environments, install only the requirements file for the
service you are running. Each service's Dockerfile uses its own directory as
its build context. The host uses `host/auth.json` locally and
`/app/state/auth.json` on its Docker volume.

The server Compose file runs the bot, database, and Caddy on the VPS. They share
the project's automatically created default Docker network. Configure the
Caddyfile with the VPS's public IP before starting all three services from the
repository root on the VPS:

```bash
sudo docker compose -f telegram-bot/compose.yaml up --build -d
```

The AI host runs on your PC and connects to the VPS over HTTPS/WSS. It needs no
external Docker network. To run the host in Docker, use an interactive terminal
on your PC, from the repository root:

```powershell
docker compose -f host/compose.yaml run --build --rm host
```

Use `DB_HOST=db` in `telegram-bot/.env` on the VPS. Set
`COORDINATOR_URL=https://YOUR_VPS_IP` in `host/.env` on your PC; there is no
implicit local-server address. Both pairing and WebSocket connections use this
address. If the model server runs directly on your Windows PC and the host runs
in Docker Desktop, use `OPEN_AI_URL=http://host.docker.internal:1234/v1` (with your
model server's port) instead of `127.0.0.1` inside the host container.
5. **Choose which chats can be processed.** Review your allowed-chat settings before using search or summaries.
6. **Use the menu.** Choose **Summarize**, **Search**, or **Settings** depending on what you need.

In **Settings → Groups**, members can see a group's required embedding model.
The group creator sets it when creating the group and can change it later.
Type `default` to use the coordinator's `EMBEDDING_MODEL`. Existing groups also
start with that default. Your own host takes priority; otherwise the bot picks
an eligible shared host from your groups. Search uses a single model and the
configured history period for both indexing and finding matches. The bot
collects missing embeddings for each chat into one request, stores each vector
with its own message, and shows how many messages it has checked. There are no
fixed message-count or character limits and no artificial delay between messages.
If the embedding API rejects the request size, the host divides it into smaller
parts without changing the order of the vectors. Restart both the bot and host
after updating, because embedding requests now also accept a list of texts.

After choosing search chats, enter your question. Then choose **Use history size**
to start immediately, or **Set date interval** and enter, for example,
`01.11.2026 - 25.11.2026`. Sending the valid interval starts the saved question
automatically. Both dates are included, using UTC calendar days.
The range applies to new indexing, saved search matches, and surrounding context.
Choose **Use history size** to return to the configured number of days.
The image-processing preference is saved per account; its additive migration
runs automatically when the bot starts. Enabling it later processes skipped
images when their messages are scanned again and rebuilds affected vectors.

Keep the coordinator's `EMBEDDING_MODEL` set to the model that produced the
existing database vectors. Change a group's model in Settings instead. Other
models' vectors are stored separately, so switching a group does not overwrite
vectors made with another model. The bot runs the additive group-model migration
on startup. Older databases must already have the forum-topic migration below.

**Example workflow:** Open the bot after a busy day → select your class group → request a summary → search for “when is the project due?” if you need a specific detail.

> **Important:** Connecting an account creates a Telegram user session, which can access Telegram on your behalf. Only log in through an instance whose operator you trust. Never send login codes or passwords to people in direct messages.

## 🔐 Privacy and security

ExtendedGram needs to read messages from conversations you authorize. It also needs to remember your Telegram login so that you aren't asked to sign in every time you search or request a summary.

### Why is a Telegram session stored?

A **session** is a digital login credential. Once you've connected your account, it lets the application talk to Telegram for you. Without a stored session, you'd need to complete the login process again and again.

Because a session is sensitive, **ExtendedGram encrypts Telegram sessions before storing them. This protection is mandatory, not an optional setting.**

### What protects the stored session?

- **Encryption:** ExtendedGram uses **AES-256-GCM** to turn the stored session into unreadable encrypted data. This also helps detect unauthorized changes to that data.
- **Password-derived protection:** It uses **PBKDF2-HMAC-SHA256**, with **600,000 iterations** and a random salt, to derive an encryption key. This makes password guessing more expensive.
- **Fresh random values:** Encryption uses randomized values so the same original text does not always produce identical encrypted output.
- **Encoding for storage:** The encrypted result is converted to a text-friendly representation using Base64. **Encoding is not encryption**; AES-GCM is what protects the contents.

**What does this mean in practice?** Someone who obtains only the encrypted session from the database should not be able to use it without the information required to decrypt it. This is an important safeguard, but it isn't a guarantee against every kind of account compromise.

### Does this mean nobody else can access my messages?

No. ExtendedGram must use the session while it is running in order to read the conversations you permit. The application operator's security practices are important, even when stored sessions are encrypted.

You can **limit which chats are processed**. This is different from limiting what access a Telegram user session technically has, so you should still use only instances you trust.

### Where does AI processing happen?

ExtendedGram can use AI models running on a local computer or a connected/shared computing host. The messages needed for a request may be sent to the host performing the processing.

If you join a group that shares computing resources, **use a host you trust**. Local AI processing can reduce reliance on external AI services, but does not by itself guarantee that messages are private from a host operator. The application may also store messages and their AI-generated representations to enable later searches.

### A few safety tips

- Use only an ExtendedGram instance operated by someone you trust.
- Select only the conversations you want the assistant to process.
- Think carefully before using shared AI hosts for sensitive conversations.
- Treat any unexpected request for Telegram credentials as suspicious.

## How ExtendedGram works

*This section is for curious users and developers. You can use the bot without understanding it.*

### Architecture diagram

```mermaid
flowchart TD
    U[You in Telegram] --> B[ExtendedGram bot]
    B <--> T[Telegram client / Telethon]
    T <--> TG[Telegram chats and messages]
    B <--> DB[(PostgreSQL + pgvector)]
    B <--> S[FastAPI coordinator]
    S <-->|WebSocket requests and results| H[Connected AI host]
    H --> L[Language model: summaries]
    H --> E[Embedding model: semantic search]
    L --> H
    E --> H
```

The diagram is simplified: it shows the key components and how they cooperate, not every internal request or database operation.

### When you ask for a summary

1. The Telegram client retrieves messages from the selected conversation.
2. ExtendedGram prepares the conversation and organizes it for AI processing.
3. An AI language model identifies related topics and produces a compact summary.
4. The bot sends the summary back to you in Telegram.

### When you search for something

1. ExtendedGram converts your question into a numerical representation of its meaning (an **embedding**).
2. It compares that representation with stored message embeddings to find related messages.
3. It retrieves surrounding messages when available, so a result isn't shown without context.
4. It returns the relevant messages through the bot.

### Why are there separate AI hosts?

Running AI models can require a powerful computer. ExtendedGram separates the bot from that heavy processing, so a connected **host** can handle AI requests and return the results.

A FastAPI backend coordinates work with hosts over WebSockets. Depending on the setup, a user can use their own host or participate in a group where computing resources are shared. **Sharing compute also means trusting the host with the content sent for processing.**

### Technology behind the project

| Purpose | Technology |
| --- | --- |
| Telegram account access | Telethon |
| Bot interface | python-telegram-bot |
| Application language | Python |
| Coordination and communication | FastAPI and WebSockets |
| Message storage | PostgreSQL |
| Meaning-based search | pgvector and embedding models |
| Summarization | Large language models through OpenAI-compatible APIs |
| Containerization | Docker |

## Project status

**Under active development.** ExtendedGram is a personal software engineering project exploring practical uses of language models, semantic search, Telegram automation, and distributed AI processing. Features and user experience may change.

## Author

Created by [Anton Chebotarov](https://github.com/tonda228).

## Running tests

For an existing database created before forum topics were added to dialog keys,
apply `telegram-bot/database/migrations/001_dialog_topics.sql` before starting
the updated bot. The migration preserves existing rows; topics omitted by the
old schema must be synchronized again. Fresh databases use the updated schema
directly. Changing `CREATE TABLE IF NOT EXISTS` does not migrate existing tables.

Install the development dependencies and run pytest from the project root:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

The tests cover encryption, Pydantic models, bot menus and authentication, host search and summaries, server host selection and request helpers, and preloading. Fake services provide responses without a running database, Telegram credentials, or an AI host.

## Processing large requests

The AI host sends all supplied messages in one model request. If the model
reports that its context is too small or truncates its answer, the host splits
the input and retries the smaller parts. A single oversized message fails
without repeatedly resending it. Fixed message-count and character limits,
including the old `LLM_BATCH_*` settings, have been removed.

The bot displays model progress and a separate combining stage when needed.
Hosts send activity updates every 15 seconds while waiting for the model. The
coordinator allows 120 seconds without activity and 30 minutes for a model request
in total. A timeout does not enqueue another copy of the same request. Restart
both the coordinator and AI host after updating their progress protocol.
Embedding batches also have a 30-minute response timeout; other short operations
retain a 120-second timeout. WebSocket messages have no fixed size limit, so a
large vector response is not cut off by the coordinator's default 16 MiB cap.

## Stopping the bot

`docker compose -f telegram-bot/compose.yaml stop bot` sends SIGTERM to the bot.
During graceful shutdown it notifies restored accounts and users who are signing
up, then stops polling and closes Telegram clients. Notifications are attempted
while the Telegram bot client is still open, for up to 20 seconds in total.
Compose gives the bot 60 seconds to stop. A forced kill or loss of power cannot
send a shutdown notification.
