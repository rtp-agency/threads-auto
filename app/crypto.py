"""Шифрование Threads-токенов (ТЗ п.1: не хранить токены в открытом виде)."""
from cryptography.fernet import Fernet

from app.config import settings

_fernet = Fernet(settings.token_encryption_key.encode()) if settings.token_encryption_key else None


def encrypt_token(token: str) -> str:
    if _fernet is None:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set")
    return _fernet.encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    if _fernet is None:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set")
    return _fernet.decrypt(encrypted.encode()).decode()
