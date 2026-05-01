# How To Run DivBots

## 1. Install

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
```

Use Python 3.11+. Python 3.12 works well on Windows.

## 2. Env

```powershell
Copy-Item .env.example .env
```

Required live values:

- `OPENAI_API_KEY`
- `TWITCH_CLIENT_ID`
- `TWITCH_CLIENT_SECRET`
- one `TWITCH_BOT_TOKEN_<BOTNAME>` per bot

Secrets live in `.env` only. Do not commit `.env`.

## 3. Bot Cast

Preferred:

```powershell
python quickstart.py
```

Quickstart:

- asks target channel, broadcaster aliases, OpenAI model
- asks Twitch app credentials
- asks Azure or HTTP transcript settings
- opens Twitch OAuth once per bot account
- writes tokens to `.env`
- writes personas/cast config to ignored `bots.local.json`

Add more bots later:

```powershell
python add_bot_assistant.py --count 3
```

Generic schema lives in `bots.example.json`. Real bot names/personas belong in `bots.local.json`.

## 4. Safe Checks

Offline:

```powershell
python -m compileall -q .
python tools/offline_check.py
python status.py
```

Safe Twitch connect; no chat sent:

```powershell
python runner.py --bot <botname> --smoketest --no-mic --no-helix
```

Send one live test message only when intended:

```powershell
python runner.py --bot <botname> --smoketest --no-mic --no-helix --send-smoketest-message
```

## 5. Live Run

```powershell
python launch_multi.py
```

Default:

- first bot handles mic + Helix
- other bots chatter-only
- shared JSON sync uses locks + atomic writes
- per-bot cooldown + global throttle active

With watchdog:

```powershell
python launch_multi.py --use-watchdog
```

With local controls:

```powershell
python launch_multi.py --inject-stdin
```

## 6. Commands

Broadcaster/mod chat commands:

```text
!divbots status
!divbots quiet
!divbots resume
!divbots stop
!divbots topic <topic>
!divbots hype
!divbots reload
!divbots forget <topic>
!divbots block <phrase>
```

Local stdin accepts same commands when `--inject-stdin` enabled.

## 7. Demo Overlay

Testing-only local overlay; does not send chat or inflate viewers.

```powershell
python -m http.server 8765
```

Open:

```text
http://127.0.0.1:8765/overlay_demo.html
```

It reads `recent_messages.json` from local repo root.

## 8. Troubleshooting

- `redirect_mismatch` → Twitch app redirect URL must exactly match quickstart redirect, usually `http://localhost:3000/callback`.
- `Malformed token` → token must start with `oauth:`.
- no bot choices → run quickstart or create `bots.local.json`.
- no transcript reactions → check Azure key or `TRANSCRIPT_HTTP_ENDPOINT`.
- no Helix/game context → check `TWITCH_CLIENT_ID` + `TWITCH_CLIENT_SECRET`.
- too much chat → raise `BASE_MIN_COOLDOWN_SECS`, lower `DIVBOTS_MAX_CAST_MESSAGES_PER_5_MIN`, or set `DIVBOTS_IDLE_ONLY=true`.
