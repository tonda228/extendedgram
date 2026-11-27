import asyncio
from contextlib import nullcontext
import signal
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telegram.error import TelegramError


def load_main(load_module, monkeypatch):
    dependencies = {
        "bot.command_handlers": ("process_button", "process_message"),
        "bot.commands.start": ("start",),
        "bot.commands.logout": ("log_out_request",),
        "features.search": ("search_request",),
        "features.summarize": ("summarize_request",),
        "server.server_runner": ("create_api_handlers",),
        "settings": ("settings",),
        "utils.infinite_task": ("send_shutdown_notification",),
        "utils.initialize_users": ("initialize_users",),
    }
    for name, attributes in dependencies.items():
        dependency = ModuleType(name)
        for attribute in attributes:
            setattr(dependency, attribute, AsyncMock())
        monkeypatch.setitem(sys.modules, name, dependency)
    monkeypatch.setattr(sys.modules["database"], "initialize_db", Mock(), raising=False)
    module = load_module("main.py")
    module.application = SimpleNamespace(
        initialize=AsyncMock(), start=AsyncMock(), stop=AsyncMock(), shutdown=AsyncMock(), running=True,
        updater=SimpleNamespace(start_polling=AsyncMock(), stop=AsyncMock(), running=True),
    )
    monkeypatch.setattr(module, "user_info", {})
    return module


@pytest.mark.parametrize("notification_error", [None, TelegramError("Unavailable"), TimeoutError()])
def test_shutdown_notifies_before_closing_bot_even_if_notification_fails(load_module, run_async, monkeypatch, notification_error):
    module = load_main(load_module, monkeypatch)
    events = []
    application = module.application
    application.updater.stop.side_effect = lambda: events.append("polling stopped")
    application.stop.side_effect = lambda: events.append("bot stopped")
    application.shutdown.side_effect = lambda: events.append("bot shutdown")
    client = SimpleNamespace(disconnect=AsyncMock(side_effect=lambda: events.append("client disconnected")))
    module.user_info[123] = SimpleNamespace(user_id=123, client=client)

    async def notify(**kwargs):
        assert kwargs == {"immediate": True}
        events.append("notified")
        if notification_error is not None:
            raise notification_error

    module.send_shutdown_notification.side_effect = notify

    async def flow():
        async with module.app.router.lifespan_context(module.app):
            application.initialize.assert_awaited_once()
            module.initialize_users.assert_awaited_once()
            application.updater.start_polling.assert_awaited_once_with(drop_pending_updates=True)
            module.send_shutdown_notification.assert_not_awaited()
        assert events == ["notified", "polling stopped", "bot stopped", "client disconnected", "bot shutdown"]

    run_async(flow())
    module.send_shutdown_notification.assert_awaited_once_with(immediate=True)


def test_startup_failure_closes_bot_without_shutdown_broadcast(load_module, run_async, monkeypatch):
    module = load_main(load_module, monkeypatch)
    module.application.running = False
    module.application.updater.running = False
    module.initialize_users.side_effect = RuntimeError("Startup failed")

    async def flow():
        with pytest.raises(RuntimeError, match="Startup failed"):
            async with module.app.router.lifespan_context(module.app):
                pytest.fail("Startup should fail before accepting requests")

    run_async(flow())
    module.application.shutdown.assert_awaited_once()
    module.send_shutdown_notification.assert_not_awaited()


@pytest.mark.parametrize("first_error", [TelegramError("Blocked"), TimeoutError()])
def test_shutdown_notification_reaches_other_users_when_one_send_fails(load_module, run_async, monkeypatch, first_error):
    module = load_module("utils/infinite_task.py")
    monkeypatch.setattr(module, "user_info", {123: object(), 456: object()})
    monkeypatch.setattr(module, "uninitialized_users", {123: object(), 789: object()})
    module.send_message.side_effect = [first_error, None, None]

    run_async(module.send_shutdown_notification(immediate=True))

    calls = module.send_message.await_args_list
    assert len(calls) == 3
    assert {call.kwargs["chat_id"] for call in calls} == {123, 456, 789}
    assert all("is shutting down" in call.kwargs["text"] and "5 minutes" not in call.kwargs["text"] for call in calls)


def test_uvicorn_sigterm_handler_runs_bot_shutdown_lifespan(load_module, run_async, monkeypatch):
    module = load_main(load_module, monkeypatch)
    server = module.uvicorn.Server(module.uvicorn.Config(module.app, host="127.0.0.1", port=0, log_level="warning"))
    # Exercise Uvicorn's exit handler without sending SIGTERM to pytest itself.
    monkeypatch.setattr(server, "capture_signals", nullcontext)

    async def flow():
        task = asyncio.create_task(server.serve())
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)
            server.handle_exit(signal.SIGTERM, None)
            await asyncio.wait_for(task, timeout=5)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    run_async(flow())
    module.send_shutdown_notification.assert_awaited_once_with(immediate=True)
    module.application.shutdown.assert_awaited_once()


def test_server_allows_large_embedding_responses(load_module, run_async, monkeypatch):
    module = load_main(load_module, monkeypatch)
    module.create_api_handlers = Mock()
    server = SimpleNamespace(serve=AsyncMock())
    factory = Mock(return_value=server)
    monkeypatch.setattr(module.uvicorn, "Server", factory)

    run_async(module.run_server())

    config = factory.call_args.kwargs["config"]
    assert config.ws_max_size is None
    server.serve.assert_awaited_once()
