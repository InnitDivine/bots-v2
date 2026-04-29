# bots-v2

Multi-account Twitch chat bot system using TwitchIO, OpenAI, Twitch Helix, optional Azure STT, and shared local JSON state.

## Setup

Install Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

Create your local env file:

```powershell
Copy-Item .env.example .env
```

Fill `.env` with your Twitch, OpenAI, and bot token values. Do not commit `.env`.

Generate Twitch bot tokens:

```powershell
python generate_twitch_bot_tokens.py --redirect-uri http://localhost:3000/callback --write-env
```

## Run

Safe connect-only check:

```powershell
python runner.py --bot sienna --smoketest --no-mic --no-helix
```

Launch all bots:

```powershell
python launch_multi.py
```

Watchdog mode:

```powershell
python launch_multi.py --use-watchdog
```

## Notes

- Tokens must use the `oauth:` prefix.
- Runtime files, logs, caches, shared JSON state, and `.env` are ignored.
- Smoketest does not send chat unless `--send-smoketest-message` is passed.

Full instructions: [docs/HOW_TO_RUN.md](docs/HOW_TO_RUN.md)
