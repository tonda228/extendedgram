import base64

import pytest
from cryptography.exceptions import InvalidTag

from utils.security import decrypt_string, derive_key, encrypt_string


@pytest.mark.parametrize("text", ["telegram-session", "", "Ahoj! 👋 Привет"])
def test_encryption_round_trip(text):
    encrypted = encrypt_string(text, "my-password")

    assert encrypted != text
    assert decrypt_string(encrypted, "my-password") == text


def test_same_text_has_different_encrypted_values():
    first = encrypt_string("session", "my-password")
    second = encrypt_string("session", "my-password")

    assert first != second
    assert decrypt_string(first, "my-password") == "session"
    assert decrypt_string(second, "my-password") == "session"


def test_wrong_password_cannot_decrypt():
    encrypted = encrypt_string("session", "correct-password")

    with pytest.raises(InvalidTag):
        decrypt_string(encrypted, "wrong-password")


def test_modified_encrypted_data_cannot_decrypt():
    encrypted = encrypt_string("session", "my-password")
    data = bytearray(base64.urlsafe_b64decode(encrypted))
    data[-1] ^= 1
    modified = base64.urlsafe_b64encode(data).decode("ascii")

    with pytest.raises(InvalidTag):
        decrypt_string(modified, "my-password")


def test_key_depends_on_password_and_salt():
    salt = b"0123456789abcdef"
    key = derive_key("my-password", salt)

    assert len(key) == 32
    assert key == derive_key("my-password", salt)
    assert key != derive_key("other-password", salt)
    assert key != derive_key("my-password", b"fedcba9876543210")
