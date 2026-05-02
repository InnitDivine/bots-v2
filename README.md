# DivBots

DivBots is a multi-account Twitch chat bot system.

It runs several Twitch bot accounts as a visible bot cast: distinct personas for banter, dead-chat prompts, hype/fail reactions, streamer conversation support, and transcript-reactive chat. DivBots is not viewbotting, followbotting, fake metrics, or hidden impersonation.

## Install

Python 3.11+ recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python quickstart.py
```

Quickstart writes secrets to `.env` and local bot cast config to ignored `bots.local.json`. Keep `bots.example.json` generic.

## Run

Safe connect check; no chat sent:

```powershell
python runner.py --bot <botname> --smoketest --no-mic --no-helix
```

Launch all configured bots:

```powershell
python launch_multi.py
```

Launch with one cast planner so only one bot reacts per shared trigger:

```powershell
python launch_multi.py --orchestrator
```

Status/doctor:

```powershell
python status.py
python launch_multi.py --status
python tools/offline_check.py
python orchestrator.py --once
```

Local control via primary window:

```powershell
python launch_multi.py --inject-stdin
```

Commands:

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

Chat commands are intended for broadcaster/moderators. Local stdin accepts them for testing.

## Safety

- `.env` stores secrets.
- `bots.local.json` stores real local bot cast config.
- `bots.example.json` stays generic.
- Smoketest does not send unless `--send-smoketest-message`.
- Runtime JSON, logs, caches, heartbeats, and `.env` ignored.
- Tokens must use `oauth:` prefix.
- Policy envs: `DIVBOTS_REAL_CHAT_SUPPRESSION_SECONDS`, `DIVBOTS_MAX_CAST_MESSAGES_PER_5_MIN`, `DIVBOTS_IDLE_ONLY`, `DIVBOTS_ASCII_ONLY`, `DIVBOTS_ALLOW_BOT_TO_BOT`.
- Orchestrator envs: `DIVBOTS_ORCHESTRATOR_TICK_SECS`, `DIVBOTS_ORCHESTRATOR_HINT_TTL_SECS`, `DIVBOTS_ORCHESTRATOR_IDLE_SILENCE_SECS`.
- Viewer memory lives in ignored `DIVBOTS_VIEWERS_FILE`.
- Optional LLM judge is off unless `DIVBOTS_USE_JUDGE=true`.

More: [docs/HOW_TO_RUN.md](docs/HOW_TO_RUN.md)
