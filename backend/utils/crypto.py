"""
Encrypt/decrypt helpers for secrets stored at rest (BYO LLM API keys, OAuth
refresh tokens). Uses Fernet (symmetric, authenticated) keyed off
FRIS_ENCRYPTION_KEY so credentials in Postgres aren't stored in plaintext.
"""

from cryptography.fernet import Fernet

from config import settings

_fernet = Fernet(settings.ENCRYPTION_KEY.encode() if isinstance(settings.ENCRYPTION_KEY, str) else settings.ENCRYPTION_KEY)


def encrypt(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode()).decode()
