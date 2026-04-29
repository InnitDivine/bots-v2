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

Use these steps if you already have Python installed:

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

## Install Guide

### Required System Installs

1. Python

Install Python from <https://www.python.org/downloads/windows/>.

During install, enable:

- `Add python.exe to PATH`
- `pip`

Check it works:

```powershell
python --version
python -m pip --version
```

2. Git

Install Git from <https://git-scm.com/download/win>.

Check it works:

```powershell
git --version
```

3. Project dependencies

From the project folder:

```powershell
cd "D:\bots v2"
python -m pip install -r requirements.txt
```

### Optional System Installs

GitHub CLI is optional. It is only needed if you want to create repos, log in to GitHub, or push through `gh`:

```powershell
winget install --id GitHub.cli -e --source winget
gh auth login
```

Azure Speech is optional at runtime if you use HTTP transcript polling instead of Azure mic transcription.

## What The Python Packages Do

- `aiohttp`: async HTTP client/session support used by Helix and runtime services.
- `twitchio`: Twitch chat bot framework.
- `openai`: OpenAI API client for generated chat/persona text.
- `azure-cognitiveservices-speech`: optional Azure speech-to-text support.
- `python-dotenv`: loads local `.env` configuration.
- `pytest`: test runner.
- `pytest-asyncio`: async test support for runner/OAuth behavior.

Install all of them with:

```powershell
python -m pip install -r requirements.txt
```

## Account And API Setup

### Twitch

Create or use a Twitch Developer application at <https://dev.twitch.tv/console/apps>.

Set a redirect URL matching the token helper command, for example:

```text
http://localhost:3000/callback
```

Required `.env` values:

```text
TWITCH_CLIENT_ID=...
TWITCH_CLIENT_SECRET=...
```

Generate bot chat tokens:

```powershell
python generate_twitch_bot_tokens.py --redirect-uri http://localhost:3000/callback --write-env
```

The helper writes token values into `.env` and does not print them. Tokens must look like:

```text
oauth:...
```

### OpenAI

Create an API key in the OpenAI dashboard, then add it to `.env`:

```text
OPENAI_API_KEY=...
```

Optional model override:

```text
OPENAI_MODEL=gpt-4o-mini
```

### Azure Speech Or HTTP Transcript

Azure Speech values are optional unless you want direct Azure STT:

```text
AZURE_SPEECH_KEY=...
AZURE_SPEECH_REGION=westus
```

HTTP transcript polling can be configured with:

```text
TRANSCRIPT_HTTP_ENDPOINT=http://127.0.0.1:5001/latest-transcript
```

## Common Commands

Install/update dependencies:

```powershell
python -m pip install -r requirements.txt
```

Run tests:

```powershell
python -m compileall -q .
python -m pytest -q
```

Safe smoketest without sending chat:

```powershell
python runner.py --bot sienna --smoketest --no-mic --no-helix
```

Send one live smoketest message only when intended:

```powershell
python runner.py --bot sienna --smoketest --no-mic --no-helix --send-smoketest-message
```

Run all bots:

```powershell
python launch_multi.py
```

Run watchdog mode:

```powershell
python launch_multi.py --use-watchdog
```

## Docs

See [docs/HOW_TO_RUN.md](docs/HOW_TO_RUN.md) for full setup, token generation, smoketest, watchdog, and manual transcript injection instructions.
