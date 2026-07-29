import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, WebSocket
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from app import auth_store, hermes_data, pty_bridge  # noqa: E402  (import after load_dotenv so env overrides apply)
from app.auth_middleware import BasicAuthMiddleware  # noqa: E402

logger = logging.getLogger("hermes_lite_chat")

STATIC_DIR = Path(__file__).parent / "static"

# Max chunks of PTY output buffered towards the browser before the PTY reader
# is paused (backpressure; nothing is ever dropped).
OUTPUT_QUEUE_MAXSIZE = 64
# Terminal geometry bounds — anything outside is garbage, not a resize.
MIN_ROWS = MIN_COLS = 1
MAX_ROWS = MAX_COLS = 500

# Hermes Agent session ids look like "20260719_072205_1e0023". Anything outside
# this shape never reaches the `hermes` argv list. Two properties matter:
# the first character must be alphanumeric, so argparse can never mistake the
# id for an option, and the whole thing must be short and free of separators.
# (The exec itself is already a plain argv list with no shell, so this is
# defence in depth rather than the only thing standing between us and injection.)
SESSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
# `hermes sessions delete` is a local sqlite delete; it has no business taking
# this long, and a hung child must not pin a threadpool worker forever.
HERMES_CLI_TIMEOUT_SECONDS = 30
# Upper bound on how much child stderr is echoed back to the browser.
MAX_CLI_ERROR_CHARS = 300


def _startup_checks() -> None:
    """Environment fail-fast + first-run bootstrap.

    Runs from the lifespan handler rather than at import time, so importing
    app.main (tests, `python -m app.main`, --reload workers) doesn't re-run it.
    """
    if not shutil.which(pty_bridge.HERMES_BIN):
        raise RuntimeError(
            f"hermes CLI not found ('{pty_bridge.HERMES_BIN}'). "
            "Install Hermes Agent or set HERMES_BIN to its path."
        )

    if not hermes_data.STATE_DB_PATH.exists():
        raise RuntimeError(
            f"Hermes state db not found at {hermes_data.STATE_DB_PATH}. "
            "Set HERMES_HOME if Hermes Agent lives somewhere other than ~/.hermes."
        )

    # Only seeds the store the first time it doesn't exist yet — a password
    # changed later via /api/auth/change-password survives restarts.
    admin_password = os.environ.get("ADMIN_PASSWORD", auth_store.PUBLIC_DEFAULT_PASSWORD)
    created = auth_store.ensure_bootstrap(
        os.environ.get("ADMIN_USERNAME", "admin"),
        admin_password,
    )
    if created and admin_password == auth_store.PUBLIC_DEFAULT_PASSWORD:
        _warn_default_password()


def _warn_default_password() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    lines = [
        "SECURITY WARNING: default password in use",
        "",
        f"This install was bootstrapped with the public default password"
        f" ({auth_store.PUBLIC_DEFAULT_PASSWORD!r}),",
        "which is published in .env.example and the README — anyone can read it.",
        f"The server is binding to HOST={host}"
        + (" (reachable from the whole network)." if host in ("0.0.0.0", "::") else "."),
        "",
        "Change it NOW via the 'Change password' form in the web UI,",
        "or set ADMIN_PASSWORD in .env and delete",
        f"{auth_store.STORE_PATH}",
        "before restarting.",
    ]
    width = max(len(line) for line in lines) + 2
    print("\n" + "!" * (width + 2), flush=True)
    for line in lines:
        print(f"!{line.center(width)}!", flush=True)
    print("!" * (width + 2) + "\n", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _startup_checks()
    yield


app = FastAPI(title="hermes-lite-chat", lifespan=lifespan)
app.add_middleware(BasicAuthMiddleware)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@app.post("/api/auth/change-password")
async def change_password(req: ChangePasswordRequest):
    if len(req.new_password) < 4:
        raise HTTPException(status_code=400, detail="New password too short")
    loop = asyncio.get_running_loop()
    changed = await loop.run_in_executor(
        None, auth_store.change_password, req.current_password, req.new_password
    )
    if not changed:
        raise HTTPException(status_code=401, detail="Current password incorrect")
    return {"ok": True}


@app.post("/api/auth/logout")
async def logout():
    """Target for the browser-side Basic Auth logout trick — see app.js.

    There is no server-side session to destroy: this app is pure HTTP Basic
    Auth, so "logged in" is a fact about the *browser's* credential cache, not
    about any state we hold. The only thing that dislodges that cache is a 401
    for the realm, which the auth middleware already produces for any request
    carrying bad credentials.

    So this handler intentionally does nothing. Its whole job is to be a
    stable, side-effect-free URL behind the auth gate that the frontend can
    deliberately hit with a *wrong* Authorization header to provoke that 401.
    Reached with good credentials (the middleware let it through) it just
    confirms the endpoint exists.

    Note it deliberately does NOT clear auth_store's verified-credential
    cache: that cache is a scrypt shortcut keyed by credential pair, not a
    session table, and flushing it would only make the next request slower
    for every client without logging anyone out.
    """
    return {"ok": True}


@app.get("/api/hermes/model")
async def get_model():
    return hermes_data.get_current_model()


@app.get("/api/hermes/sessions")
async def get_sessions(limit: int = Query(50, ge=1, le=500)):
    return hermes_data.list_sessions(limit=limit)


def _run_hermes(*args: str) -> subprocess.CompletedProcess:
    """Run the same `hermes` binary the PTY bridge spawns, non-interactively.

    Argument *list*, never a shell string (no shell=True anywhere), so nothing
    in the arguments can be interpreted as shell syntax. stdin is /dev/null so
    a CLI that unexpectedly decides to prompt fails fast instead of hanging.
    """
    return subprocess.run(
        [pty_bridge.HERMES_BIN, *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=HERMES_CLI_TIMEOUT_SECONDS,
        cwd=str(Path.home()),
        env=pty_bridge.child_env(),
    )


def _cli_error_detail(proc: subprocess.CompletedProcess, fallback: str) -> str:
    """Last meaningful line the CLI printed, trimmed to something showable."""
    for stream in (proc.stderr, proc.stdout):
        lines = [line.strip() for line in (stream or "").splitlines() if line.strip()]
        if lines:
            return lines[-1][:MAX_CLI_ERROR_CHARS]
    return fallback


@app.delete("/api/hermes/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a Hermes Agent session by shelling out to the real CLI.

    Deliberately not a `DELETE FROM sessions` against state.db: this app only
    ever *reads* Hermes Agent's own files (see hermes_data), and every
    mutation goes through the CLI that owns them, so related rows, indexes and
    any future bookkeeping stay consistent.
    """
    if not SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="Invalid session id")

    loop = asyncio.get_running_loop()
    try:
        proc = await loop.run_in_executor(
            None, _run_hermes, "sessions", "delete", "--yes", session_id
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail=f"hermes CLI not found ('{pty_bridge.HERMES_BIN}').",
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail=f"hermes sessions delete timed out after {HERMES_CLI_TIMEOUT_SECONDS}s.",
        )
    except OSError as exc:
        logger.warning("hermes sessions delete failed to start: %r", exc)
        raise HTTPException(status_code=500, detail="Could not run the hermes CLI.")

    if proc.returncode != 0:
        logger.info(
            "hermes sessions delete %s exited %s: %s",
            session_id,
            proc.returncode,
            (proc.stderr or proc.stdout or "").strip()[:500],
        )
        raise HTTPException(
            status_code=502,
            detail=_cli_error_detail(
                proc, f"hermes sessions delete exited {proc.returncode}."
            ),
        )

    return {"ok": True, "id": session_id}


@app.websocket("/ws/pty")
async def ws_pty(websocket: WebSocket, session_id: str | None = None):
    await websocket.accept()

    proc, master_fd = pty_bridge.spawn_hermes_pty(session_id)
    loop = asyncio.get_running_loop()

    out_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=OUTPUT_QUEUE_MAXSIZE)
    eof_event = asyncio.Event()
    pending_write = bytearray()
    state = {"reader_paused": False, "writer_registered": False, "reader_added": False}
    writer_task: asyncio.Task | None = None
    eof_task: asyncio.Task | None = None

    # --- PTY -> browser -----------------------------------------------------

    def pause_reader() -> None:
        if state["reader_added"] and not state["reader_paused"]:
            loop.remove_reader(master_fd)
            state["reader_paused"] = True

    def resume_reader() -> None:
        if state["reader_added"] and state["reader_paused"]:
            state["reader_paused"] = False
            loop.add_reader(master_fd, on_readable)

    def on_readable() -> None:
        # Never read what we can't buffer: pausing the reader applies
        # backpressure to the PTY instead of dropping output.
        if out_queue.full():
            pause_reader()
            return
        try:
            data = os.read(master_fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            # EOF: the child exited or closed the pty. Stop reading and let the
            # main loop tear the session down (so the child gets reaped).
            with contextlib.suppress(ValueError, OSError):
                loop.remove_reader(master_fd)
            state["reader_added"] = False
            eof_event.set()
            return
        out_queue.put_nowait(data)
        if out_queue.full():
            pause_reader()

    async def pump_output() -> None:
        while True:
            chunk = await out_queue.get()
            resume_reader()
            if chunk is None:
                return
            try:
                await websocket.send_bytes(chunk)
            except Exception as exc:  # connection gone / send failed
                logger.info("pty->ws send failed: %r", exc)
                eof_event.set()
                return

    # --- browser -> PTY -----------------------------------------------------

    def flush_pending() -> None:
        while pending_write:
            try:
                written = os.write(master_fd, pending_write)
            except BlockingIOError:
                return
            except OSError as exc:
                logger.info("pty write failed: %r", exc)
                pending_write.clear()
                break
            del pending_write[:written]
        if state["writer_registered"]:
            with contextlib.suppress(ValueError, OSError):
                loop.remove_writer(master_fd)
            state["writer_registered"] = False

    def write_to_pty(data: bytes) -> None:
        """Write everything, buffering whatever the PTY won't take right now.

        A single os.write() on a non-blocking pty master both short-writes
        (large pastes) and raises BlockingIOError once its buffer is full.
        """
        if pending_write:
            pending_write.extend(data)
            return
        view = memoryview(data)
        while view:
            try:
                written = os.write(master_fd, view)
            except BlockingIOError:
                pending_write.extend(view)
                if not state["writer_registered"]:
                    loop.add_writer(master_fd, flush_pending)
                    state["writer_registered"] = True
                return
            except OSError as exc:
                logger.info("pty write failed: %r", exc)
                return
            view = view[written:]

    try:
        os.set_blocking(master_fd, False)
        loop.add_reader(master_fd, on_readable)
        state["reader_added"] = True
        writer_task = asyncio.ensure_future(pump_output())
        eof_task = asyncio.ensure_future(eof_event.wait())

        while True:
            recv_task = asyncio.ensure_future(websocket.receive())
            done, _ = await asyncio.wait(
                {recv_task, eof_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if recv_task not in done:
                # Child exited first: drain what it printed, then close the WS.
                recv_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await recv_task
                break

            message = recv_task.result()
            if message["type"] == "websocket.disconnect":
                break

            data = message.get("bytes")
            if data is not None:
                write_to_pty(data)
                continue

            text = message.get("text")
            if text is not None:
                try:
                    control = json.loads(text)
                except ValueError:
                    continue
                if not isinstance(control, dict) or control.get("type") != "resize":
                    continue
                try:
                    # int(float('inf')) raises OverflowError, int({}) TypeError
                    rows = int(control.get("rows", 24))
                    cols = int(control.get("cols", 80))
                except (TypeError, ValueError, OverflowError):
                    continue
                if not (MIN_ROWS <= rows <= MAX_ROWS and MIN_COLS <= cols <= MAX_COLS):
                    continue
                pty_bridge.resize(master_fd, rows, cols)
    finally:
        if eof_task is not None:
            eof_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await eof_task
        if state["reader_added"]:
            with contextlib.suppress(ValueError, OSError):
                loop.remove_reader(master_fd)
            state["reader_added"] = False
        if state["writer_registered"]:
            with contextlib.suppress(ValueError, OSError):
                loop.remove_writer(master_fd)
            state["writer_registered"] = False
        if writer_task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(_stop_pump(out_queue, writer_task), timeout=5)
            if not writer_task.done():
                writer_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await writer_task
        with contextlib.suppress(Exception):
            await websocket.close()
        await loop.run_in_executor(None, pty_bridge.terminate, proc, master_fd)


async def _stop_pump(queue: asyncio.Queue, task: asyncio.Task) -> None:
    """Let the output pump flush what's queued, then stop it."""
    await queue.put(None)
    await task


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", 8000)),
        reload=os.environ.get("RELOAD", "").lower() in ("1", "true", "yes"),
    )
