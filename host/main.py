import asyncio
from contextlib import suppress
from getpass import getpass
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
import websockets
from dotenv import load_dotenv
import httpx
import openai
from websockets import ConnectionClosed
from websockets.asyncio.client import ClientConnection

from host.host.search import process_search_request
from host.host.summarize import process_summarize_request
from host.llm.completions import translate_image
from host.llm.embeddings import create_embedding, create_embeddings
from host.utils.embedding_models import parse_embedding_models

load_dotenv()
AUTH_FILE = Path(os.getenv("AUTH_FILE", str(Path(__file__).with_name("auth.json"))))
COORDINATOR_URL = os.getenv("COORDINATOR_URL", "").rstrip("/")


def get_configured_embedding_models() -> tuple[str, ...]:
    configured = os.getenv("EMBEDDING_MODELS")
    models = [model.strip() for model in configured.split(",")] if configured else [os.getenv("EMBEDDING_MODEL")]
    return parse_embedding_models(models)

def offer_another_user() -> bool:
    while True:
        new_user = input("Do you want to connect another account? [y/N]:")
        if new_user.lower() in ('y', 'yes'):
            return True
        if new_user.lower() in ('', 'n', 'no'):
            return False
        else:
            print("Invalid choice")

async def get_token() -> list[tuple[int, str, str]]:
    new_users = []
    while True:
        while True:
            try:
                user_id = int(input("Please enter your user_id: "))
                pairing_code = input("Please enter one-time code: ")
            except ValueError:
                print("Invalid user_id. Try again.")
            else:
                break
        password = getpass("Please enter your bot password: ")
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{COORDINATOR_URL}/pair", json={
                "user_id": user_id,
                "code": pairing_code,
                "password": password,
            })
            response.raise_for_status()
            info = response.json()
            new_users.append((user_id, info["token"], password))

        if not offer_another_user():
            return new_users

def store_new_users(users_data, new_users):
    accounts = {account["user_id"]: account for account in users_data}
    for new_user in new_users:
        user_id, token, password = new_user
        accounts[user_id] = {
            "user_id": user_id,
            "token": token,
            "password": password
        }
    users_data[:] = list(accounts.values())

    with AUTH_FILE.open("w", encoding="utf-8") as auth_file:
        json.dump(users_data, auth_file)


def remove_saved_user(user_id: int) -> None:
    try:
        with AUTH_FILE.open(encoding="utf-8") as auth_file:
            users_data = json.load(auth_file)
    except FileNotFoundError:
        return
    users_data = [account for account in users_data if account["user_id"] != user_id]
    with AUTH_FILE.open("w", encoding="utf-8") as auth_file:
        json.dump(users_data, auth_file)

async def send_error(websocket, request_id: int):
    await websocket.send(json.dumps({"status": 404, "request_id": request_id}))

async def keep_host_running(websocket: ClientConnection, embedding_models: tuple[str, ...] | None = None):
    if embedding_models is None:
        embedding_models = get_configured_embedding_models()
    try:
        status = json.loads(await websocket.recv())
    except (json.JSONDecodeError, UnicodeDecodeError):
        print("Invalid connection status")
        return
    if not isinstance(status, dict) or status.get("status") != 200:
        print("Connection failed")
        return

    print("Success")

    while True:
        raw_request = await websocket.recv()
        try:
            request = json.loads(raw_request)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(request, dict):
            continue
        request_id = request.get("request_id")
        if not isinstance(request_id, int):
            continue
        if not isinstance(request.get("user_id"), int) or "data" not in request:
            await send_error(websocket, request_id)
            continue

        operation = request.get("operation")
        data = request["data"]
        if operation == "image" and not isinstance(data, str):
            await send_error(websocket, request_id)
            continue
        if operation == "embedding" and not (isinstance(data, str) or (
            isinstance(data, list) and data and all(isinstance(item, str) and item for item in data)
        )):
            await send_error(websocket, request_id)
            continue
        if operation in ("search", "summarize") and (
            not isinstance(data, list) or not all(isinstance(item, dict) for item in data)
        ):
            await send_error(websocket, request_id)
            continue

        progress_state = (0, max(1, len(data)), "messages")

        async def report_progress(completed: int, total: int, stage: str) -> None:
            nonlocal progress_state
            progress_state = (completed, total, stage)
            await websocket.send(json.dumps({
                "status": 102, "request_id": request_id,
                "user_id": request["user_id"], "operation": operation,
                "completed": completed, "total": total, "stage": stage,
            }))

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(15)
                await report_progress(*progress_state)

        heartbeat_task = None
        if operation in ("search", "summarize"):
            await report_progress(*progress_state)
            heartbeat_task = asyncio.create_task(heartbeat())
        try:
            if operation == "summarize":
                result = await process_summarize_request(request["data"], progress=report_progress)
                if len(result) == 0:
                    await send_error(websocket, request["request_id"])
                    continue
            elif operation == "search":
                text = request.get("search_text")
                if not isinstance(text, str) or not text:
                    await send_error(websocket, request["request_id"])
                    continue
                result = await process_search_request(request["data"], text, progress=report_progress)
            elif operation == "embedding":
                model = request.get("embedding_model", embedding_models[0])
                if model not in embedding_models:
                    await send_error(websocket, request_id)
                    continue
                result = (await create_embeddings(data, model) if isinstance(data, list)
                          else await create_embedding(data, model))
            elif operation == "image":
                result = await translate_image(request["data"])
            else:
                await send_error(websocket, request["request_id"])
                continue
        except (ValueError, TypeError, KeyError, openai.OpenAIError) as error:
            print(f"Request {request_id} ({operation}) failed: {error}")
            await send_error(websocket, request["request_id"])
            continue

        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError, ConnectionClosed):
                    await heartbeat_task

        await websocket.send(json.dumps({
            "status": 200,
            "user_id": request["user_id"],
            "request_id": request["request_id"],
            "operation": request["operation"],
            "result": result
        }))

async def open_connection(user_data: dict[str, int|str]):
    user_id = int(user_data["user_id"])
    token = user_data["token"]
    password = user_data["password"]
    try:
        embedding_models = get_configured_embedding_models()
    except ValueError as error:
        print(f"Invalid embedding model configuration: {error}")
        return

    try:
        coordinator = urlsplit(COORDINATOR_URL)
        websocket_scheme = {"http": "ws", "https": "wss"}[coordinator.scheme]
        websocket_url = coordinator._replace(scheme=websocket_scheme).geturl()
        websocket = await websockets.connect(f"{websocket_url}/ws/{user_id}",
                                             additional_headers={
                                                 "Authorization": f"Bearer {token}",
                                                 "Password": password,
                                                 "Embedding-Models": json.dumps(embedding_models),
                                                 "Embedding-Model": embedding_models[0],
                                             },
                                             max_size=None)
    except Exception as e:
        print("Authentication failed or server is unavailable.")
        print(e)
        return

    worker = asyncio.create_task(keep_host_running(websocket, embedding_models))
    closed = asyncio.create_task(websocket.wait_closed())
    try:
        done, _ = await asyncio.wait((worker, closed), return_when=asyncio.FIRST_COMPLETED)
        if worker in done:
            await worker
    except (ConnectionClosed, OSError) as error:
        print(f"Disconnected: {error}")
    finally:
        worker.cancel()
        closed.cancel()
        await asyncio.gather(worker, closed, return_exceptions=True)
        if websocket.close_code == 4001:
            remove_saved_user(user_id)
        with suppress(ConnectionClosed, OSError):
            await websocket.close()

async def open_connections() -> None:
    if not COORDINATOR_URL:
        print("Set COORDINATOR_URL in host/.env to your server address, for example https://YOUR_VPS_IP.")
        return
    if AUTH_FILE.is_file():
        with AUTH_FILE.open(encoding="utf-8") as auth_file:
            users_data = json.load(auth_file)
    else:
        users_data = []

    if not users_data or offer_another_user():
        try:
            new_users = await get_token()
            store_new_users(users_data, new_users)
        except httpx.HTTPStatusError:
            print("Pairing failed. Check your user ID, pairing code and bot password. "
                  "Request a new code in Settings if the code expired or was used.")
            return
        except httpx.RequestError:
            print("Could not reach the coordinator. Check COORDINATOR_URL and make sure the bot is running.")
            return

    connections = [open_connection(user_data) for user_data in users_data]
    await asyncio.gather(*connections)

if __name__ == "__main__":
    try:
        asyncio.run(open_connections())
    except KeyboardInterrupt:
        print("\nHost stopped.")
    except EOFError:
        print("Terminal input was closed. Run the host interactively to enter account details.")
