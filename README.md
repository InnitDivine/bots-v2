# bots-v2

Safe, testable multi-account Twitch chat bot system.

This project runs multiple TwitchIO bot accounts against one Twitch channel. It can use OpenAI for chat generation, Twitch Helix for stream context, Azure Speech or HTTP transcript polling for broadcaster context, and locked shared JSON files for coordination between bot processes.

## What It Includes

- Multi-bot launcher and per-bot runner.
- Twitch OAuth token helper with state validation.
- OpenAI persona generation helper for adding bot accounts.
- Shared transcript, metadata, and recent-message coordination.
- Per-bot cooldowns plus global cross-bot throttle.
- Secret-safe defaults for smoketests, logs, caches, and runtime files.
- Pytest coverage for config safety, OAuth helpers, runner restart behavior, shared JSON safety, and chat sanitation.

## Safety Notes

- Do not commit `.env`.
- Keep OAuth tokens, API keys, logs, and account credentials local.
- Use `.env.example` as the only committed env template.
- Runtime files under `run/`, shared JSON state, caches, and Python cache files are ignored.
- `runner.py --smoketest` does not send chat unless `--send-smoketest-message` is passed.

## Quick Start

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill `.env` with:

- `OPENAI_API_KEY`
- `TWITCH_CLIENT_ID`
- `TWITCH_CLIENT_SECRET`
- `TWITCH_BOT_TOKEN_*` values with `oauth:` prefix

Generate Twitch bot tokens:

```powershell
python generate_twitch_bot_tokens.py --redirect-uri http://localhost:3000/callback --write-env
```

Run safe checks:

```powershell
python -m compileall -q .
python -m pytest -q
```

Run a safe connect-only smoketest:

```powershell
python runner.py --bot sienna --smoketest --no-mic --no-helix
```

Launch all bots:

```powershell
python launch_multi.py
```

## Docs

See [docs/HOW_TO_RUN.md](docs/HOW_TO_RUN.md) for full setup, token generation, smoketest, watchdog, and manual transcript injection instructions.
