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
- `ADMIN_USERNAME` / `ADMIN_PASSWORD` — **bootstrap-only** login (default `admin` / `pass123!`), used just the first time the app runs. Change it from the sidebar ("Change password") afterwards — the real credentials then live hashed (scrypt) in `~/.config/hermes-lite-chat/auth.json`, and editing `.env` after that has no effect.

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

- The whole app (static assets, API, and the terminal WebSocket) sits behind HTTP Basic Auth. Anyone who *does* log in has the same capabilities as a local terminal session with `hermes` — full tool/shell/browser access. Only run this on a network you trust, and change the default password.
- Closing the tab kills that PTY's `hermes chat` process (it doesn't "pause" — resuming the same session later continues from whatever was already saved to `state.db`).
- Two tabs resuming the *same* session concurrently behave like two terminals attached to the same `--resume` id — governed by Hermes Agent's own active-session logic, not this app.
- `/api/hermes/model` and `/api/hermes/sessions` are read-only lookups (YAML/SQLite); this app never writes to Hermes Agent's config or session database directly.

---

# hermes-lite-chat (wersja polska)

Terminal webowy dla lokalnej instalacji [Hermes Agent](https://hermes-agent.nousresearch.com/) (`~/.hermes`). Uruchamia prawdziwe `hermes chat --cli` w PTY i strumieniuje je do terminala `xterm.js` w przeglądarce — to nie jest reimplementacja, tylko dosłownie ten sam program CLI — dzięki czemu korzystanie z narzędzi, prompty zatwierdzania i strumieniowanie zachowują się dokładnie tak samo jak w prawdziwym terminalu. Pasek boczny wyświetla listę istniejących sesji Hermesa (tylko do odczytu, bezpośrednio z `~/.hermes/state.db`), dzięki czemu można wznowić dowolną z nich albo rozpocząć nową rozmowę.

Aplikacja nie ma własnych kluczy API: Hermes Agent zarządza swoimi danymi uwierzytelniającymi i konfiguracją modelu (`~/.hermes/config.yaml`) samodzielnie.

## Instalacja

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Plik `.env` kontroluje:
- `HERMES_HOME` / `HERMES_BIN` — potrzebne tylko wtedy, gdy Hermes Agent jest zainstalowany gdzie indziej niż `~/.hermes`, albo `hermes` nie jest dostępne w `PATH`.
- `HOST` (domyślnie `0.0.0.0`) / `PORT` (domyślnie `8000`) — własny adres nasłuchu tej aplikacji, **niezależny od dashboardu Hermes Agent na porcie 9119** (brak jakiegokolwiek powiązania czy konfliktu — obie usługi mogą działać jednocześnie). `HOST=0.0.0.0` udostępnia aplikację innym urządzeniom w Twojej sieci lokalnej; użyj `HOST=127.0.0.1`, żeby ograniczyć dostęp tylko do tej maszyny.
- `RELOAD=true` — opcjonalny tryb deweloperski z automatycznym przeładowaniem przy zmianie plików.
- `ADMIN_USERNAME` / `ADMIN_PASSWORD` — login **tylko na pierwsze uruchomienie** (domyślnie `admin` / `pass123!`), używany wyłącznie przy pierwszym starcie aplikacji. Zmień go później z poziomu paska bocznego ("Change password") — od tego momentu prawdziwe dane logowania są przechowywane jako hash (scrypt) w `~/.config/hermes-lite-chat/auth.json`, a edycja `.env` nie ma już żadnego znaczenia.

## Uruchomienie

```bash
python -m app.main
```

Automatycznie odczytuje `HOST`/`PORT` (oraz `RELOAD`) z pliku `.env`. Otwórz `http://<adres-IP-tej-maszyny-w-sieci-lokalnej>:8000` z dowolnego urządzenia w Twojej sieci (albo `http://localhost:8000` z tej samej maszyny).

Alternatywnie, jeśli wolisz bezpośrednio CLI uvicorn (`HOST`/`PORT` z `.env` *nie* są wtedy odczytywane automatycznie — trzeba podać je jawnie jako flagi):

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Uwagi i zastrzeżenia

- Cała aplikacja (pliki statyczne, API oraz WebSocket terminala) jest zabezpieczona uwierzytelnianiem HTTP Basic Auth. Każdy, kto *faktycznie* się zaloguje, ma te same możliwości co lokalna sesja terminalowa `hermes` — pełny dostęp do narzędzi, powłoki i przeglądarki. Uruchamiaj tę aplikację wyłącznie w sieci, której ufasz, i koniecznie zmień domyślne hasło.
- Zamknięcie karty przeglądarki kończy proces `hermes chat` powiązany z daną sesją PTY (to nie jest "pauza" — wznowienie tej samej sesji później kontynuuje od tego, co zdążyło się zapisać do `state.db`).
- Dwie karty wznawiające jednocześnie **tę samą** sesję zachowują się jak dwa terminale podłączone pod to samo `--resume` id — reguluje to własna logika aktywnej sesji Hermes Agent, a nie ta aplikacja.
- `/api/hermes/model` i `/api/hermes/sessions` to zapytania wyłącznie do odczytu (YAML/SQLite); ta aplikacja nigdy nie zapisuje bezpośrednio do konfiguracji ani bazy sesji Hermes Agent.
