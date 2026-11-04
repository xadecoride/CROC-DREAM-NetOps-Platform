from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

TOKEN_BYTES = 32
_ENC_PREFIX = "enc:v1:"


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    # Токен случайный и длинный, поэтому быстрого SHA-256 достаточно.
    return hashlib.sha256(token.encode()).hexdigest()


def _resolve_master_key(key_material: str | None = None) -> bytes:
    if key_material and key_material.strip():
        raw = key_material.strip()
    else:
        raw = os.getenv("NETOPS_MASTER_KEY", "").strip()
    if not raw:
        try:
            from netops.settings import get_settings

            s = get_settings()
            secret_attr = getattr(s, "secret_key", None)
            if secret_attr is not None:
                raw = (
                    secret_attr.get_secret_value()
                    if hasattr(secret_attr, "get_secret_value")
                    else str(secret_attr)
                ).strip()
        except Exception:
            _ = None
    if not raw:
        raw = "croc-dream-netops-envelope-default-key-v1"
    return hashlib.sha256(raw.encode("utf-8")).digest()


def encrypt_secret(plaintext: str, key_material: str | None = None) -> str:
    if not plaintext:
        return ""
    if plaintext.startswith("enc:"):
        return plaintext
    key_bytes = _resolve_master_key(key_material)
    try:
        from cryptography.fernet import Fernet

        f_key = base64.urlsafe_b64encode(key_bytes)
        token = Fernet(f_key).encrypt(plaintext.encode("utf-8")).decode("ascii")
        return f"{_ENC_PREFIX}{token}"
    except Exception:
        salt = secrets.token_bytes(16)
        data = plaintext.encode("utf-8")
        stream_key = hashlib.pbkdf2_hmac("sha256", key_bytes, salt, 10000, dklen=len(data))
        cipher = bytes(a ^ b for a, b in zip(data, stream_key, strict=True))
        mac = hmac.new(key_bytes, salt + cipher, hashlib.sha256).digest()
        blob = base64.urlsafe_b64encode(salt + mac + cipher).decode("ascii")
        return f"{_ENC_PREFIX}std:{blob}"


def decrypt_secret(token: str, key_material: str | None = None) -> str:
    if not token or not token.startswith("enc:"):
        return token
    payload = token[len(_ENC_PREFIX) :] if token.startswith(_ENC_PREFIX) else token[4:]
    key_bytes = _resolve_master_key(key_material)
    if payload.startswith("std:"):
        raw = base64.urlsafe_b64decode(payload[4:].encode("ascii"))
        salt, mac, cipher = raw[:16], raw[16:48], raw[48:]
        expected_mac = hmac.new(key_bytes, salt + cipher, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected_mac):
            raise ValueError("Invalid secret MAC")
        stream_key = hashlib.pbkdf2_hmac("sha256", key_bytes, salt, 10000, dklen=len(cipher))
        return bytes(a ^ b for a, b in zip(cipher, stream_key, strict=True)).decode("utf-8")

    from cryptography.fernet import Fernet

    f_key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(f_key).decrypt(payload.encode("ascii")).decode("utf-8")
