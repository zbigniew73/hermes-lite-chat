"""Spawn the real `hermes chat --cli` in a PTY and bridge it to a WebSocket.

Deliberately not a re-implementation of Hermes Agent's chat behavior: this
runs the actual CLI binary the user already uses in a terminal, so tool use,
approval prompts, and streaming all behave identically. --tui (the Ink/Node
UI) is intentionally not used, and no --source override is passed, so
sessions started from the web are indistinguishable from ones started in a
real terminal.
"""

import fcntl
import os
import pty
import shutil
import signal
import struct
import subprocess
import termios
from pathlib import Path

HERMES_BIN = os.environ.get("HERMES_BIN") or shutil.which("hermes") or "hermes"

# This app's own login secrets have no business being visible to the agent
# process (or to anything the agent shells out to).
_STRIPPED_ENV_VARS = ("ADMIN_USERNAME", "ADMIN_PASSWORD", "HERMES_LITE_AUTH_STORE")


def child_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in _STRIPPED_ENV_VARS:
        env.pop(name, None)
    return env


def spawn_hermes_pty(session_id: str | None) -> tuple[subprocess.Popen, int]:
    master_fd, slave_fd = pty.openpty()
    cmd = [HERMES_BIN, "chat", "--cli"]
    if session_id:
        cmd += ["--resume", session_id]

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            # start_new_session does the setsid() in the C-level child helper;
            # preexec_fn runs Python after fork(), which is unsafe in a
            # threaded server (uvicorn's executor).
            start_new_session=True,
            cwd=str(Path.home()),
            env=child_env(),
        )
    except BaseException:
        for fd in (slave_fd, master_fd):
            try:
                os.close(fd)
            except OSError:
                pass
        raise
    os.close(slave_fd)
    return proc, master_fd


def resize(master_fd: int, rows: int, cols: int) -> None:
    packed = struct.pack("HHHH", rows, cols, 0, 0)
    try:
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, packed)
    except OSError:
        pass


def terminate(proc: subprocess.Popen, master_fd: int) -> None:
    """Kill the whole process group so no orphaned hermes process lingers."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        pgid = None

    if pgid is not None:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass

    try:
        os.close(master_fd)
    except OSError:
        pass
