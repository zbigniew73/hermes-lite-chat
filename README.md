# hermes-lite-chat

A minimal web chat client for the Hermes LLM family, served via [OpenRouter](https://openrouter.ai/). FastAPI backend proxies streamed chat completions and hides the API key from the browser; frontend is plain HTML/CSS/JS with no build step.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set `OPENROUTER_API_KEY` to your OpenRouter key. Optionally change `OPENROUTER_MODEL` (default: `nousresearch/hermes-3-llama-3.1-405b`) to any other Hermes variant available on OpenRouter.

## Run

```bash
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 in your browser.

## Notes

- Conversation history is kept in the browser tab only (no database/persistence).
- `/api/chat` streams Server-Sent Events straight through from OpenRouter — the API key never reaches the client.
- `/healthz` reports the currently configured model.
