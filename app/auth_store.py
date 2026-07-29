"""hermes-lite-chat's own login store (separate from Hermes Agent's auth).

Stored outside the repo tree so it never gets committed, hashed (scrypt,
salted) rather than kept in plaintext. `ensure_bootstrap` only seeds the
store the first time it doesn't exist yet, so a password changed via the UI
survives app restarts instead of being reset back to the .env default.
"""

import hashlib
import json
import os
import secrets
from pathlib import Path

STORE_PATH = Path(
    os.environ.get("HERMES_LITE_AUTH_STORE", "~/.config/hermes-lite-chat/auth.json")
).expanduser()


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)


def _write(username: str, password: str) -> None:
    salt = secrets.token_bytes(16)
    digest = _hash_password(password, salt)
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(
        json.dumps({"username": username, "salt": salt.hex(), "hash": digest.hex()})
    )
    STORE_PATH.chmod(0o600)


def ensure_bootstrap(default_username: str, default_password: str) -> None:
    if not STORE_PATH.exists():
        _write(default_username, default_password)


def verify(username: str, password: str) -> bool:
    if not STORE_PATH.exists():
        return False
    data = json.loads(STORE_PATH.read_text())
    if not secrets.compare_digest(username, data["username"]):
        return False
    salt = bytes.fromhex(data["salt"])
    expected = bytes.fromhex(data["hash"])
    actual = _hash_password(password, salt)
    return secrets.compare_digest(actual, expected)


def change_password(current_password: str, new_password: str) -> bool:
    data = json.loads(STORE_PATH.read_text())
    if not verify(data["username"], current_password):
        return False
    _write(data["username"], new_password)
    return True
