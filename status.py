import json
import time
from pathlib import Path
from typing import Any

from config import (
    BOTS,
    CROSSBOT_RECENT_FILE,
    HELIX_CACHE_FILE,
    HEARTBEAT_DIR,
    SHARED_META_FILE,
    SHARED_TRANSCRIPT_FILE,
    TARGET_CHANNEL,
    TRANSCRIPT_HTTP_ENDPOINT,
    AZURE_SPEECH_KEY,
    validate_bot_configs,
)


def _read_json(path: str) -> Any:
    try:
        p = Path(path)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _age(path: Path) -> str:
    try:
        return f"{time.time() - path.stat().st_mtime:.0f}s ago"
    except OSError:
        return "missing"


def _recent_count() -> int:
    data = _read_json(CROSSBOT_RECENT_FILE)
    if isinstance(data, dict) and isinstance(data.get("messages"), list):
        now = time.time()
        return sum(1 for row in data["messages"] if now - float(row.get("ts", 0)) <= 60)
    return 0


def main() -> None:
    meta = _read_json(SHARED_META_FILE) or {}
    transcript = _read_json(SHARED_TRANSCRIPT_FILE)
    helix = _read_json(HELIX_CACHE_FILE)
    errors = validate_bot_configs(BOTS)
    hb_dir = Path(HEARTBEAT_DIR)

    print("DivBots Status")
    print(f"channel: {TARGET_CHANNEL}")
    print(f"bots: {len(BOTS)}")
    for bot in BOTS:
        modes = ",".join(bot.get("message_modes", []))
        print(
            f" - {bot['name']} role={bot.get('role', 'cast_member')} "
            f"mod={bot.get('is_moderator', False)} modes={modes}"
        )
    print(f"bot config: {'ok' if not errors else 'warn'}")
    for err in errors:
        print(f" - {err}")

    transcript_source = "azure" if AZURE_SPEECH_KEY else f"http endpoint {TRANSCRIPT_HTTP_ENDPOINT}"
    print(f"transcript source: {transcript_source}")
    print(f"transcript file: {SHARED_TRANSCRIPT_FILE}")
    print(f"transcript state: {'present' if isinstance(transcript, dict) else 'missing'}")
    print(f"meta file: {SHARED_META_FILE}")
    print(f"recent file: {CROSSBOT_RECENT_FILE}")
    print(f"global throttle count last 60s: {_recent_count()}")
    print(f"helix cache: {'present' if helix is not None else 'missing'} ({HELIX_CACHE_FILE})")
    print(f"last game: {meta.get('game', 'unknown') if isinstance(meta, dict) else 'unknown'}")
    print(
        "state: "
        f"hype={meta.get('hype', 0) if isinstance(meta, dict) else 0} "
        f"emotion={meta.get('detected_emotion', 'neutral') if isinstance(meta, dict) else 'neutral'} "
        f"help={meta.get('help_mode', False) if isinstance(meta, dict) else False} "
        f"quiet_until={meta.get('quiet_until', 0) if isinstance(meta, dict) else 0} "
        f"stopped={meta.get('stopped', False) if isinstance(meta, dict) else False}"
    )

    print("heartbeats:")
    if hb_dir.exists():
        for path in sorted(hb_dir.glob("*.heartbeat")):
            print(f" - {path.name}: {_age(path)}")
    else:
        print(" - none")


if __name__ == "__main__":
    main()
