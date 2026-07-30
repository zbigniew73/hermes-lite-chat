# =============================================================================
# hermes-lite-chat
# =============================================================================
# Strona   : automation-ai.eu - Automatyzacja AI
# Autor    : Zbigniew Czechowski
# WWW      : https://automation-ai.eu/
# E-mail   : kontakt@automation-ai.eu
# Licencja : MIT
# =============================================================================

"""hermes-lite-chat's own login store (separate from Hermes Agent's auth).

Stored outside the repo tree so it never gets committed, hashed (scrypt,
salted) rather than kept in plaintext. `ensure_bootstrap` only seeds the
store the first time it doesn't exist yet, so a password changed via the UI
survives app restarts instead of being reset back to the .env default.
"""

import contextlib
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

STORE_PATH = Path(
    os.environ.get("HERMES_LITE_AUTH_STORE", "~/.config/hermes-lite-chat/auth.json")
).expanduser()

# The password shipped in .env.example / README. Kept as the bootstrap default
# on purpose, but its use is loudly flagged at startup (see main.py).
PUBLIC_DEFAULT_PASSWORD = "pass123!"

# Fixed salt used when there is nothing real to hash against, so a bad username
# (or an unreadable store) still costs exactly one scrypt call. Without it,
# "wrong username" would answer measurably faster than "wrong password".
_DUMMY_SALT = b"hermes-lite-chat-dummy-salt-0001"[:16]
_DUMMY_HASH = b"\x00" * 32

# --- verification cache -----------------------------------------------------
# scrypt is deliberately expensive (~100ms), and Basic Auth re-sends the same
# credentials on *every* request (including every static asset). Cache the
# successful outcome briefly, keyed by a fast keyed hash of the credential
# pair so no plaintext password sits in memory in a recoverable form.
_CACHE_TTL_SECONDS = 60.0
_CACHE_MAX_ENTRIES = 128
_cache_seed = secrets.token_bytes(32)
_verify_cache: dict[bytes, float] = {}


def _credential_key(username: str, password: str) -> bytes:
    digest = hashlib.blake2b(key=_cache_seed, digest_size=32)
    digest.update(username.encode("utf-8", "surrogatepass"))
    digest.update(b"\x00")
    digest.update(password.encode("utf-8", "surrogatepass"))
    return digest.digest()


def cached_verify(username: str, password: str) -> bool:
    """True only if this exact credential pair verified successfully recently."""
    try:
        key = _credential_key(username, password)
    except Exception:
        return False
    expires_at = _verify_cache.get(key)
    if expires_at is None:
        return False
    if expires_at <= time.monotonic():
        _verify_cache.pop(key, None)
        return False
    return True


def remember_verified(username: str, password: str) -> None:
    try:
        key = _credential_key(username, password)
    except Exception:
        return
    now = time.monotonic()
    for stale_key in [k for k, exp in _verify_cache.items() if exp <= now]:
        _verify_cache.pop(stale_key, None)
    if len(_verify_cache) >= _CACHE_MAX_ENTRIES:
        _verify_cache.clear()
    _verify_cache[key] = now + _CACHE_TTL_SECONDS


def clear_verify_cache() -> None:
    _verify_cache.clear()


# --- brute-force throttling --------------------------------------------------
# Per-client-IP attempt tracking, in memory only (resets on restart, same as
# the verification cache above). This is a last-resort backstop against a
# single naive brute-force loop hammering this process directly — it is not a
# substitute for network-level protections when this app sits behind a
# reverse proxy (see the README's security recommendations), since every
# request may then appear to come from the proxy's own IP.
_FAILURE_WINDOW_SECONDS = 60.0
_FAILURE_THRESHOLD = 10
_MAX_TRACKED_CLIENTS = 256
_failures: dict[str, list[float]] = {}


def _prune(times: list[float], now: float) -> None:
    cutoff = now - _FAILURE_WINDOW_SECONDS
    while times and times[0] < cutoff:
        times.pop(0)


def is_locked_out(client_host: str) -> bool:
    times = _failures.get(client_host)
    if not times:
        return False
    _prune(times, time.monotonic())
    return len(times) >= _FAILURE_THRESHOLD


def record_attempt(client_host: str) -> None:
    """Note that a credential check is about to happen, before it happens.

    Called synchronously, before the (slow, executor-hopping) real scrypt
    check — recording after the fact would let a burst of concurrent guesses
    all start before any single one finishes and gets counted, sailing past
    the threshold. A successful check clears the bucket via record_success,
    so an optimistic pre-recorded attempt that turns out to be a real login
    never lingers as a false failure.
    """
    now = time.monotonic()
    if client_host not in _failures and len(_failures) >= _MAX_TRACKED_CLIENTS:
        # Evict the single stalest entry rather than clearing everyone, so an
        # attacker can't wipe their own lockout on demand just by cycling
        # through enough distinct source addresses to fill the table.
        oldest_key = min(_failures, key=lambda k: _failures[k][-1] if _failures[k] else 0.0)
        _failures.pop(oldest_key, None)
    times = _failures.setdefault(client_host, [])
    times.append(now)
    _prune(times, now)


def record_success(client_host: str) -> None:
    _failures.pop(client_host, None)


# --- store ------------------------------------------------------------------


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8", "surrogatepass"), salt=salt, n=2**14, r=8, p=1, dklen=32
    )


def _write(username: str, password: str) -> None:
    """Write the store atomically, 0600 from the moment the file exists.

    write_text() + chmod() would leave the file at the process umask
    (typically world-readable) for the window in between, and a crash or full
    disk mid-write would leave a truncated file that verify() can never read
    again — locking out the admin permanently, since ensure_bootstrap refuses
    to re-seed a path that already exists. A temp file created with the right
    mode up front, then renamed into place, has neither problem: the rename
    is atomic, so readers only ever see a complete file or none at all.
    """
    salt = secrets.token_bytes(16)
    digest = _hash_password(password, salt)
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"username": username, "salt": salt.hex(), "hash": digest.hex()})
    tmp_path = STORE_PATH.with_name(f".{STORE_PATH.name}.tmp-{os.getpid()}")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, STORE_PATH)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp_path)
        raise


def ensure_bootstrap(default_username: str, default_password: str) -> bool:
    """Seed the store if absent. Returns True if a new store was created."""
    if STORE_PATH.exists():
        return False
    _write(default_username, default_password)
    return True


def verify(username: str, password: str) -> bool:
    """Constant-ish time credential check. Never raises."""
    try:
        try:
            data = json.loads(STORE_PATH.read_text())
            stored_username = str(data["username"])
            salt = bytes.fromhex(data["salt"])
            expected = bytes.fromhex(data["hash"])
        except (OSError, ValueError, KeyError, TypeError):
            stored_username, salt, expected = "", _DUMMY_SALT, _DUMMY_HASH

        # Compare as bytes: compare_digest raises TypeError on non-ASCII str.
        username_ok = secrets.compare_digest(
            username.encode("utf-8", "surrogatepass"),
            stored_username.encode("utf-8", "surrogatepass"),
        )

        # Always run scrypt, whichever half is wrong, so the two failure modes
        # are indistinguishable by response time.
        actual = _hash_password(password, salt if username_ok else _DUMMY_SALT)
        password_ok = secrets.compare_digest(
            actual, expected if username_ok else _DUMMY_HASH
        )
        return username_ok and password_ok
    except Exception:
        return False


def change_password(current_password: str, new_password: str) -> bool:
    try:
        data = json.loads(STORE_PATH.read_text())
        username = str(data["username"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if not verify(username, current_password):
        return False
    _write(username, new_password)
    # Old credentials must stop working immediately, cache or no cache.
    clear_verify_cache()
    return True
