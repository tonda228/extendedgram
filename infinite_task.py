import asyncio

infinite_task = None

async def get_infinite_task():
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        print("Task was successfully finished.")