# =============================================================================
# hermes-lite-chat
# =============================================================================
# Strona   : automation-ai.eu - Automatyzacja AI
# Autor    : Zbigniew Czechowski
# WWW      : https://automation-ai.eu/
# E-mail   : kontakt@automation-ai.eu
# Licencja : MIT
# =============================================================================

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


# Only these keys are ever echoed back to the browser. Whitelisting rather
# than returning the whole `model:` subtree means a future config addition
# (an api_key:, say) can't leak out through this endpoint.
MODEL_PUBLIC_FIELDS = ("default", "provider")


def get_current_model() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            config = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(config, dict):
        return {}
    model = config.get("model")
    if not isinstance(model, dict):
        return {}
    return {k: model[k] for k in MODEL_PUBLIC_FIELDS if k in model}


def list_sessions(limit: int = 50) -> list[dict]:
    uri = f"{STATE_DB_PATH.as_uri()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        # e.g. db missing, or WAL side-files not creatable in read-only mode
        return []
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
    except sqlite3.Error:
        return []
    finally:
        con.close()

