# How To Run

## Safety Defaults

- Keep secrets in `.env` only. Commit/share `.env.example`, not `.env`.
- Runtime files under `run/`, logs, heartbeats, caches, and shared JSON state are ignored.
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
- one `TWITCH_BOT_TOKEN_<BOTNAME>` value per bot

TwitchIO tokens must use `oauth:` prefix.
Real bot accounts and AI-generated personas are stored in ignored `bots.local.json`. The tracked `bots.example.json` is only a generic sample.

## Guided Quickstart

Run this for first setup:

```powershell
python quickstart.py
```

It asks for Twitch, OpenAI, optional Azure or HTTP transcript settings, then lets you add bot accounts one by one. Each bot login uses the same OAuth callback flow as `generate_twitch_bot_tokens.py`, and tokens are written to `.env` without being printed.

## Generate Twitch Bot Tokens

Register redirect URI in Twitch app settings, then run:

```powershell
python generate_twitch_bot_tokens.py --redirect-uri http://localhost:3000/callback --write-env
```

The helper validates OAuth state, writes `oauth:`-prefixed tokens, and does not print token values.

## Add Bot Accounts

To add several bots and let AI generate a short role/persona for each one:

```powershell
python add_bot_assistant.py --count 3
```

The assistant opens a Twitch login for each bot account, writes tokens to `.env`, and writes each bot plus its AI-generated role to ignored `bots.local.json`.

## Smoke Test

Safe default:

```powershell
python runner.py --bot <botname> --smoketest --no-mic --no-helix
```

Send one live test chat message only when intended:

```powershell
python runner.py --bot <botname> --smoketest --no-mic --no-helix --send-smoketest-message
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

## Syntax Check

```powershell
python -m compileall -q .
```
