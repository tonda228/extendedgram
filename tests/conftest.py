import asyncio
from collections.abc import Awaitable, Callable
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

ROOT = Path(__file__).resolve().parents[1] / "telegram-bot"


@pytest.fixture
def load_module(monkeypatch):
    """Replace services before importing modules that connect on import."""
    dependencies = {
        "bot": dict(application=SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))),
        "database": dict(cur=Mock(), connection=Mock()),
        "database.dialogs": dict(delete_unused_channels=Mock(), get_recent_dialogs=AsyncMock(), get_allowed_dialogs=AsyncMock(), get_all_dialogs=AsyncMock(), store_dialog=Mock(), get_unread_count=Mock(), update_dialog_priorities=Mock(), delete_old_dialog_priorities=Mock()),
        "database.messages": dict(store_unsaved_messages=AsyncMock(), store_message=AsyncMock(), delete_public_message=Mock(), delete_private_message=Mock(), get_best_public_messages=Mock(return_value=[]), get_best_private_messages=Mock(return_value=[]), get_public_messages_for_summarization=Mock(return_value=[]), get_private_messages_for_summarization=Mock(return_value=[])),
        "database.groups": dict(get_app_user_groups=Mock(return_value=[]), update_user_groups=AsyncMock(), get_affected_users_and_groups=Mock(return_value={}), delete_group=Mock()),
        "features.preloading": dict(reset_idle_timer=Mock()),
        "utils.check_authentication": dict(check_authentication=AsyncMock(return_value=True), offer_group_options=AsyncMock()),
        "utils.helpers": dict(send_message=AsyncMock(), get_user_name=Mock(return_value="Anton"), get_request_data=Mock(return_value=[]), resend_processing_status=AsyncMock(), get_topic_id=Mock(return_value=0), get_message_info=Mock(), get_full_chat_name=Mock(return_value="Chat")),
        "llm.completions": dict(send_data=AsyncMock(), translate_image=AsyncMock(), ProgressCallback=Callable[[int, int, str], Awaitable[None]]),
        "llm.embeddings": dict(create_embedding=AsyncMock()),
        "llm": dict(llm_client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())))),
        "database.users": dict(store_telegram_user=Mock(), store_app_user=Mock(), delete_unused_users=Mock(), delete_app_user=Mock()),
        "features.event_handlers": dict(create_new_message_handler=AsyncMock(), create_delete_message_handler=AsyncMock()),
        "server": dict(get_host=AsyncMock(return_value=None), select_host=Mock(return_value=None), active_requests={}, available_hosts={}, available_group_hosts={}, update_host_priorities=Mock(), disconnect_host=AsyncMock(), close_websocket=AsyncMock(), finish_requests=AsyncMock()),
        "server.requests": dict(send_request=AsyncMock(), request_media_description=AsyncMock(), request_embedding=AsyncMock(), request_message_embedding=AsyncMock()),
        "bot.commands.menu": dict(menu=AsyncMock()),
    }
    for name, attributes in dependencies.items():
        fake = ModuleType(name)
        fake.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, fake)
    features = ModuleType("features")
    features.event_handlers = sys.modules["features.event_handlers"]
    monkeypatch.setitem(sys.modules, "features", features)
    monkeypatch.setenv("API_ID", "1")
    monkeypatch.setenv("API_HASH", "test-hash")
    monkeypatch.setenv("COMPLETIONS_MODEL", "test-model")
    monkeypatch.setenv("EMBEDDING_MODEL", "test-embedding")
    monkeypatch.delenv("EMBEDDING_MODELS", raising=False)

    def load(path):
        package = str(Path(path).parent).replace("\\", ".").replace("/", ".")
        spec = importlib.util.spec_from_file_location(f"{package}.module_under_test", ROOT / path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return load


@pytest.fixture
def run_async():
    """Run an async function from a normal pytest test."""
    return asyncio.run
