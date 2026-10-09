import os
import base64

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def derive_key(password: str, salt: bytes):
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=600000
    )

    return kdf.derive(password.encode())

def encrypt_string(text: str, password: str):
    salt = os.urandom(16)
    nonce = os.urandom(12)

    key = derive_key(password, salt)

    aes = AESGCM(key)

    cipher_text = aes.encrypt(nonce, text.encode(), None)
    data = salt + nonce + cipher_text
    return base64.urlsafe_b64encode(data).decode("ascii")

def decrypt_string(data: str, password: str):
    data = base64.urlsafe_b64decode(data)

    salt = data[:16]
    nonce = data[16:28]
    cipher_text = data[28:]

    key = derive_key(password, salt)
    aes = AESGCM(key)
    decrypted_text = aes.decrypt(nonce, cipher_text, None)

    return decrypted_text.decode()