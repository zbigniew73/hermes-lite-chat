"""Read-only helpers for the local Hermes Agent install (~/.hermes).

Nothing here ever writes to state.db or config.yaml — session listing and
model info are read-only lookups. Continuing a conversation happens by
spawning the real `hermes` CLI in a PTY (see pty_bridge.py), not by touching
Hermes Agent's data files directly.
"""

import os
import sqlite3
from pathlib import Path

import yaml

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
CONFIG_PATH = HERMES_HOME / "config.yaml"
STATE_DB_PATH = HERMES_HOME / "state.db"


def get_current_model() -> dict:
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)
    return config.get("model", {})


def list_sessions(limit: int = 50) -> list[dict]:
    uri = f"file:{STATE_DB_PATH}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT id, source, display_name, title, model,
                   started_at, ended_at, message_count, archived
            FROM sessions
            WHERE archived = 0
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()
