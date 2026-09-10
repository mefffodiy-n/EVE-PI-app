"""
Шифрование токенов ESI перед записью в БД (roadmap: Fernet).

Ключ — в `PI_TOKEN_KEY` (переменная окружения, не в git). Отсутствие
ключа не роняет импорт: падает только при попытке зашифровать/расшифровать,
с внятным сообщением. Так приложение поднимается и без SSO.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from infra import config


class TokenCryptoError(RuntimeError):
    """Токен не удалось зашифровать или расшифровать."""


def generate_key() -> str:
    """Новый ключ Fernet (base64). Положить в PI_TOKEN_KEY."""
    return Fernet.generate_key().decode("ascii")


def _fernet() -> Fernet:
    key = config.TOKEN_ENCRYPTION_KEY
    if not key:
        raise TokenCryptoError(
            "PI_TOKEN_KEY не задан — токены ESI шифровать нечем. "
            "Сгенерируйте: python -c \"from infra.crypto import generate_key; "
            "print(generate_key())\""
        )
    try:
        return Fernet(key.encode("ascii") if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise TokenCryptoError(f"PI_TOKEN_KEY некорректен: {exc}") from exc


def encrypt(plaintext: str) -> str:
    """Строка → зашифрованная строка для хранения в БД."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    """Обратно. Ключ сменили или данные битые → TokenCryptoError."""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise TokenCryptoError(
            "Токен не расшифровывается: сменился PI_TOKEN_KEY или запись повреждена. "
            "Персонажу нужно заново войти через EVE SSO."
        ) from exc
