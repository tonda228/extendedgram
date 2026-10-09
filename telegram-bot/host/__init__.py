import asyncio
import json
import os
from glob import translate

import websockets
from dotenv import load_dotenv
import httpx

from host.search import process_search_request
from host.summarize import process_summarize_request
from llm.completions import translate_image
from llm.embeddings import create_embedding

load_dotenv()

async def get_token():
    while True:
        try:
            user_id = int(input("Please enter your user_id: "))
            pairing_code = input("Please enter one-time code: ")
        except ValueError:
            print("Invalid user_id. Try again.")
        else:
            break
    async with httpx.AsyncClient() as client:
        response = await client.post(f"http://localhost:8000/pair", json={
            "user_id": user_id,
            "code": pairing_code
        })
        response.raise_for_status()
        info = response.json()
        return user_id, info["token"], info["password"]

async def open_connection():
    if not os.path.exists("auth.json"):
        try:
            user_id, token, password = await get_token()
            with open("auth.json", "w") as auth_file:
                json.dump({
                    "user_id": user_id,
                    "token": token,
                    "password": password
                }, auth_file)
        except httpx.HTTPStatusError:
            print("Invalid user_id or pairing code.")
            return
    else:
        with open("auth.json") as auth_file:
            auth_info = json.load(auth_file)
            user_id = auth_info["user_id"]
            token = auth_info["token"]
            password = auth_info["password"]
    try:
        websocket = await websockets.connect(f"ws://localhost:8000/ws/{user_id}",
                                             additional_headers={"Authorization": f"Bearer {token}",
                                                                 "Password": password},
                                             max_size=None)
    except Exception as e:
        print("Authentication failed or server is unavailable.")
        print(e)
        return

    result = await websocket.recv()
    if json.loads(result)["status"] != 200:
        print("Connection failed")
        return

    print("Success")
    while True:
        request = await websocket.recv()
        request = json.loads(request)

        result = []
        operation = request.get("operation")

        if operation == "summarize":
            try:
                result = await process_summarize_request(request["data"])
            except json.decoder.JSONDecodeError:
                await websocket.send(json.dumps({"status": 404}))
                continue
        elif operation == "search":
            text = request.get("search_text")
            if not text:
                await websocket.send(json.dumps({"status": 404}))
                continue
            result = await process_search_request(request["data"], text)
        elif operation == "embedding":
            result = await create_embedding(request["data"])
        elif operation == "image":
            result = await translate_image(request["data"])
        else:
            await websocket.send(json.dumps({"status": 404}))
            continue

        await websocket.send(json.dumps({
            "status": 200,
            "user_id": request["user_id"],
            "request_id": request["request_id"],
            "operation": request["operation"],
            "result": result
        }))

if __name__ == "__main__":
    asyncio.run(open_connection())
