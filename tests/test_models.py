from datetime import datetime, timezone
import asyncio

import numpy as np
import pytest
from pgvector import Vector
from pydantic import ValidationError

from utils.classes import (
    AppUser, DialogDBObject, LoginStage, PrivateMessageDBObject,
    PublicMessageDBObject, UninitializedUser, UserGroupDBObject, UserState,
)


def test_new_user_starts_at_role_choice():
    user = UninitializedUser()

    assert user.status == LoginStage.AWAIT_ROLE_CHOICE
    assert user.tries_left == 5
    assert user.phone_num is None
    assert user.group is None


def test_login_cooldown_blocks_only_while_timer_is_running(run_async):
    async def check():
        user = UninitializedUser()
        user.tries_left = 0
        timer = user._init_info.wait_task
        try:
            assert user.wait is True
            timer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await timer
            assert user.wait is False
        finally:
            timer.cancel()

    run_async(check())


def test_login_properties_can_be_updated():
    user = UninitializedUser()
    user.phone_num = "+420123456789"
    user.phone_code_hash = "code-hash"
    user.tries_left = 4

    assert user.phone_num == "+420123456789"
    assert user.phone_code_hash == "code-hash"
    assert user.tries_left == 4


def test_selected_group_can_be_stored_on_user():
    group = UserGroupDBObject(
        group_id=1, name="Friends", password_hash="hash", created_by=123,
    )
    user = UninitializedUser()
    user.group = group

    assert user.group.name == "Friends"
    assert user.group.password_hash == "hash"


def test_app_user_defaults():
    user = AppUser(user_id=123, status=UserState.LOGGED_OUT)

    assert user.client is None
    assert user.allow_all is True
    assert user.preloading is False
    assert user.cur_page == 0
    assert user.history_size == 100


def test_dialog_accepts_missing_channel_and_topic():
    dialog = DialogDBObject(
        dialog_id=1, user_id=123, title="Private chat", is_allowed=True,
        channel_id=None, topic_id=None,
    )

    assert dialog.channel_id is None
    assert dialog.topic_id is None


def test_dialog_rejects_invalid_id():
    with pytest.raises(ValidationError):
        DialogDBObject(
            dialog_id="not-an-id", user_id=123, title="Chat", is_allowed=True,
            channel_id=None, topic_id=None,
        )


def test_public_message_accepts_nullable_database_fields():
    message = PublicMessageDBObject(
        message_id=42, channel_id=1, topic_id=0, sender_id=None,
        date_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        text=None, media_description=None, embedding=None,
    )

    assert message.text is None
    assert message.sender_id is None
    assert message.user_name is None


def test_private_message_preserves_embedding_and_joined_fields():
    embedding = np.array([0.1, 0.2], dtype=np.float32)
    message = PrivateMessageDBObject(
        message_id=42, dialog_id=1, user_id=123, sender_id=456,
        date_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        text="Hello", media_description=None, embedding=embedding,
        title="Friends", user_name="Anton",
    )

    assert message.embedding is embedding
    assert message.title == "Friends"
    assert message.user_name == "Anton"


@pytest.mark.parametrize("model, ids", [
    (PrivateMessageDBObject, {"dialog_id": 1, "user_id": 123, "topic_id": 0}),
    (PublicMessageDBObject, {"channel_id": 1, "topic_id": 0}),
])
def test_message_models_accept_all_selected_columns(model, ids):
    # class_row passes every selected column to the model as a keyword argument.
    row = {
        "message_id": 42,
        "sender_id": 456,
        "date_time": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "text": "Hello",
        "media_description": None,
        "embedding": None,
        "title": "Friends",
        "user_name": "Anton",
        **ids,
    }

    message = model(**row)

    assert message.model_dump() == row


@pytest.mark.parametrize("model, ids", [
    (PrivateMessageDBObject, {"dialog_id": 1, "user_id": 123}),
    (PublicMessageDBObject, {"channel_id": 1, "topic_id": 0}),
])
def test_message_models_accept_database_vector(model, ids):
    embedding = Vector([0.1, 0.2])
    message = model(
        message_id=42, sender_id=None,
        date_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        text="Hello", media_description=None, embedding=embedding,
        **ids,
    )

    assert message.embedding is embedding
