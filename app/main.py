import asyncio
import json
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from app import auth_store, hermes_data, pty_bridge  # noqa: E402  (import after load_dotenv so env overrides apply)
from app.auth_middleware import BasicAuthMiddleware  # noqa: E402

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
auth_store.ensure_bootstrap(
    os.environ.get("ADMIN_USERNAME", "admin"),
    os.environ.get("ADMIN_PASSWORD", "pass123!"),
)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="hermes-lite-chat")
app.add_middleware(BasicAuthMiddleware)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@app.post("/api/auth/change-password")
async def change_password(req: ChangePasswordRequest):
    if len(req.new_password) < 4:
        raise HTTPException(status_code=400, detail="New password too short")
    if not auth_store.change_password(req.current_password, req.new_password):
        raise HTTPException(status_code=401, detail="Current password incorrect")
    return {"ok": True}


@app.get("/api/hermes/model")
async def get_model():
    return hermes_data.get_current_model()


@app.get("/api/hermes/sessions")
async def get_sessions(limit: int = 50):
    return hermes_data.list_sessions(limit=limit)


@app.websocket("/ws/pty")
async def ws_pty(websocket: WebSocket, session_id: str | None = None):
    await websocket.accept()

    proc, master_fd = pty_bridge.spawn_hermes_pty(session_id)
    loop = asyncio.get_running_loop()
    os.set_blocking(master_fd, False)

    def on_readable():
        try:
            data = os.read(master_fd, 65536)
        except OSError:
            data = b""
        if data:
            asyncio.ensure_future(websocket.send_bytes(data))
        else:
            loop.remove_reader(master_fd)

    loop.add_reader(master_fd, on_readable)

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break

            data = message.get("bytes")
            if data is not None:
                os.write(master_fd, data)
                continue

            text = message.get("text")
            if text is not None:
                try:
                    control = json.loads(text)
                except ValueError:
                    continue
                if control.get("type") == "resize":
                    pty_bridge.resize(
                        master_fd,
                        int(control.get("rows", 24)),
                        int(control.get("cols", 80)),
                    )
    finally:
        try:
            loop.remove_reader(master_fd)
        except (ValueError, OSError):
            pass
        await loop.run_in_executor(None, pty_bridge.terminate, proc, master_fd)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", 8000)),
        reload=os.environ.get("RELOAD", "").lower() in ("1", "true", "yes"),
    )
