# ExtendedGram AI host

This folder contains the AI host that runs on your computer. It connects to an
the ExtendedGram server at `https://62.83.121.11` over HTTPS/WSS. Open
[@soprano_jr_bot](https://t.me/soprano_jr_bot) in Telegram. You need a running
OpenAI-compatible model server on your computer.

## Download only the host

With Git installed, run:

```bash
git clone --filter=blob:none --sparse https://github.com/tonda228/extendedgram.git
cd extendedgram
git sparse-checkout set host
```

Git checks out the `host/` folder and top-level repository files. The bot's source
directory is omitted. Repository metadata is still downloaded so future updates
work normally. Run the remaining commands from this repository root, the folder
containing `host/`.

## Configure

On Windows PowerShell:

```powershell
Copy-Item host/.env.example host/.env
notepad host/.env
```

On Linux or macOS:

```bash
cp host/.env.example host/.env
nano host/.env
```

Set the following values:

| Variable | Meaning |
|---|---|
| `COORDINATOR_URL` | ExtendedGram server: `https://62.83.121.11`. Keep this value unchanged. |
| `OPEN_AI_URL` | Your model server's OpenAI-compatible API URL, including `/v1` |
| `OPENAI_API_KEY` | Your model server's key; `local` is sufficient only for an unauthenticated server |
| `COMPLETIONS_MODEL` | Exact model name for search answers, summaries, and enabled image descriptions |
| `EMBEDDING_MODEL` | Exact model name producing 1024-dimensional embeddings |

For multiple embedding models, uncomment `EMBEDDING_MODELS` and list their exact
names separated by commas. All advertised models must be available on your model
server. The first is used for your own requests; group requests select the model
required by the group.

When a group creator enters `default`, the group uses the server's default
embedding model. The bot displays its actual name in the group model prompt and
settings. Configure that exact name on your host; `default` is not a model name
for `.env`. Every advertised model must already be available through your model
server. Naming a missing model does not install it, and requests for it fail.

The server address is already filled in in `.env.example` and is also the default
when `COORDINATOR_URL` is missing or empty. Configure only your model server's
URL, key, and model names. The host pairs over HTTPS and connects to
`wss://62.83.121.11` automatically for WebSocket requests.
If a different server address is configured, the host exits before asking for
account credentials and tells you which address to use.

## Run with Python

Use Python 3.14, matching the project's Docker image. On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r host/requirements.txt
.\.venv\Scripts\python.exe -m host.main
```

On Linux or macOS:

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install -r host/requirements.txt
.venv/bin/python -m host.main
```

Keep your model server running while the host is connected.

## Run with Docker

If the model server runs directly on your Windows PC, set this in `host/.env`,
using its actual port:

```dotenv
OPEN_AI_URL=http://host.docker.internal:1234/v1
```

Start the host interactively:

```bash
docker compose -f host/compose.yaml run --build --rm host
```

Docker creates its own network automatically. No shared network with the VPS or
incoming port forwarding on your PC is needed. Pairing credentials are stored
in a named Docker volume and retained when the temporary container is removed.
The current host asks an interactive question at startup, so use this terminal
command rather than starting it detached.

## Pair and stop

In Telegram, complete bot signup or enter your existing bot password. Open
**Settings → Pair host**. Enter the displayed user ID, one-time code, and your
bot password in the host terminal. Pairing codes expire after 10 minutes.

On later launches, choose `n` when asked about adding another account to reuse
saved credentials. Choose `y` to pair another account or replace an old pairing.

Press Ctrl+C to stop the host. The connection closes and saved credentials are
retained. Restart the host if its connection is lost; automatic reconnection is
not implemented yet.

For a native Python run, credentials are saved in `host/auth.json`. `.env` files,
`auth.json`, Python caches, and virtual environments are ignored by Git. The
`.env.example` file contains placeholders and is intended to be committed.

## Update

From the repository root:

```bash
git pull
```

The sparse checkout remains active. Update the Python dependencies using the
same `pip install -r host/requirements.txt` command and restart the host, or
rerun the Docker command with `--build`.
