# How To Run

## Safety Defaults

- Keep secrets in `.env` only. Commit/share `.env.example`, not `.env`.
- Runtime files under `run/`, logs, heartbeats, caches, and shared JSON state are ignored.
- Tests do not require live Twitch, OpenAI, or Azure calls.
- Smoketest connects only; it does not send chat unless `--send-smoketest-message` is passed.

## Setup

1. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

2. Copy `.env.example` to `.env`, then fill required values:

```powershell
Copy-Item .env.example .env
```

Required runtime values:

- `OPENAI_API_KEY`
- `TWITCH_CLIENT_ID`
- `TWITCH_CLIENT_SECRET`
- `TWITCH_BOT_TOKEN_SIENNA`, `TWITCH_BOT_TOKEN_KNIGHT`, `TWITCH_BOT_TOKEN_SIMP`

TwitchIO tokens must use `oauth:` prefix.

## Generate Twitch Bot Tokens

Register redirect URI in Twitch app settings, then run:

```powershell
python generate_twitch_bot_tokens.py --redirect-uri http://localhost:3000/callback --write-env
```

The helper validates OAuth state, writes `oauth:`-prefixed tokens, and does not print token values.

## Smoke Test

Safe default:

```powershell
python runner.py --bot sienna --smoketest --no-mic --no-helix
```

Send one live test chat message only when intended:

```powershell
python runner.py --bot sienna --smoketest --no-mic --no-helix --send-smoketest-message
```

## Normal Run

Launch all bots in separate windows:

```powershell
python launch_multi.py
```

Default behavior:

- First bot handles mic and Helix.
- Other bots run chatter-only with `--no-mic --no-helix`.
- Bots sync through locked shared JSON files.
- Per-bot cooldown and global combined throttle limit chat spam.

## Optional Watchdog

```powershell
python launch_multi.py --use-watchdog
```

## Manual Transcript Injection

```powershell
python launch_multi.py --inject-stdin
```

Type lines into primary window at `inject>` prompt.

## Tests

```powershell
python -m compileall -q .
python -m pytest -q
```
