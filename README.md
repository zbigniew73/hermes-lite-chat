# hermes-lite-chat

A web terminal for your local [Hermes Agent](https://hermes-agent.nousresearch.com/) install (`~/.hermes`). It spawns the real `hermes chat --cli` in a PTY and streams it into an `xterm.js` terminal in the browser — not a reimplementation, the literal same CLI program — so tool use, approval prompts, and streaming all behave exactly as they do in a real terminal. The sidebar lists your existing Hermes sessions (read-only, straight from `~/.hermes/state.db`) so you can pick one to resume, or start a new chat.

No API keys of its own: Hermes Agent manages its own credentials and model config (`~/.hermes/config.yaml`).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

```bash
cp .env.example .env
```

`.env` controls:
- `HERMES_HOME` / `HERMES_BIN` — only needed if Hermes Agent lives somewhere other than `~/.hermes`, or `hermes` isn't on `PATH`.
- `HOST` (default `0.0.0.0`) / `PORT` (default `8000`) — this app's own bind address, **independent of Hermes Agent's dashboard on port 9119** (no relation, no conflict — both can run at the same time). `HOST=0.0.0.0` makes it reachable from other devices on your local network; use `HOST=127.0.0.1` to restrict it to this machine only.
- `RELOAD=true` — optional dev auto-reload on file changes.

## Run

```bash
python -m app.main
```

Reads `HOST`/`PORT` (and `RELOAD`) from `.env` automatically. Open `http://<this machine's LAN IP>:8000` from any device on your network (or `http://localhost:8000` from this machine).

Alternative, if you prefer the uvicorn CLI directly (`.env`'s `HOST`/`PORT` are *not* picked up this way — pass flags explicitly):

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Notes / caveats

- Anyone who can reach this page has the same capabilities as a local terminal session with `hermes` — full tool/shell/browser access. Only run this on a network you trust.
- Closing the tab kills that PTY's `hermes chat` process (it doesn't "pause" — resuming the same session later continues from whatever was already saved to `state.db`).
- Two tabs resuming the *same* session concurrently behave like two terminals attached to the same `--resume` id — governed by Hermes Agent's own active-session logic, not this app.
- `/api/hermes/model` and `/api/hermes/sessions` are read-only lookups (YAML/SQLite); this app never writes to Hermes Agent's config or session database directly.
