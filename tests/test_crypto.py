"""infra/crypto: шифрование токенов ESI перед записью в БД."""

from __future__ import annotations

import pytest

from infra import config, crypto
from infra.crypto import TokenCryptoError, decrypt, encrypt, generate_key


@pytest.fixture
def key(monkeypatch):
    k = generate_key()
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", k)
    return k


def test_round_trip(key):
    secret = "refresh-token-abc123"
    blob = encrypt(secret)
    assert blob != secret
    assert decrypt(blob) == secret


def test_ciphertext_is_not_plaintext(key):
    assert "sensitive" not in encrypt("sensitive-value")


def test_missing_key_fails_clearly(monkeypatch):
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", None)
    with pytest.raises(TokenCryptoError, match="PI_TOKEN_KEY"):
        encrypt("x")


def test_wrong_key_cannot_decrypt(monkeypatch):
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", generate_key())
    blob = encrypt("x")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", generate_key())
    with pytest.raises(TokenCryptoError, match="заново войти"):
        decrypt(blob)
