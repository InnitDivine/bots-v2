import json
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
TOKEN_PREFIX = "oauth:"
_TOKEN_BODY_RE = re.compile(r"^[^\s:]+$")
_TOKEN_PLACEHOLDERS = {"...", "changeme", "change_me", "replace_me", "token", "your_token"}


def _truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _load_dotenv() -> None:
    if _truthy(os.getenv("BOTS_DISABLE_DOTENV")):
        return
    dotenv_path = os.getenv("BOTS_DOTENV_PATH")
    load_dotenv(dotenv_path=dotenv_path or ROOT / ".env", override=_truthy(os.getenv("BOTS_DOTENV_OVERRIDE")))


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _path_env(name: str, default: str) -> Path:
    path = Path(_env(name, default))
    return path if path.is_absolute() else ROOT / path


def _int_env(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = _env(name, str(default))
    try:
        return float(raw)
    except ValueError:
        return default


def normalize_twitch_token(raw: str) -> str:
    token = (raw or "").strip()
    if not token:
        return ""
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if token.lower().startswith(TOKEN_PREFIX):
        body = token[len(TOKEN_PREFIX) :].strip()
    else:
        body = token
    if not body or body.lower() in _TOKEN_PLACEHOLDERS or not _TOKEN_BODY_RE.fullmatch(body):
        raise ValueError("Malformed Twitch token")
    return f"{TOKEN_PREFIX}{body}"


def is_valid_twitch_token(raw: str) -> bool:
    token = (raw or "").strip()
    if not token.lower().startswith(TOKEN_PREFIX):
        return False
    try:
        return bool(normalize_twitch_token(token))
    except ValueError:
        return False

TARGET_CHANNEL = _env("TARGET_CHANNEL", "your_channel")
PREFERRED_BROADCASTER_NAME = _env("PREFERRED_BROADCASTER_NAME", TARGET_CHANNEL)
BROADCASTER_ALIASES = [
    alias.strip()
    for alias in _env("BROADCASTER_ALIASES", f"{PREFERRED_BROADCASTER_NAME},{TARGET_CHANNEL}").split(",")
    if alias.strip()
]
if not BROADCASTER_ALIASES:
    BROADCASTER_ALIASES = [TARGET_CHANNEL or "broadcaster"]

TWITCH_CLIENT_ID = _env("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = _env("TWITCH_CLIENT_SECRET")

BOTS_FILE = _path_env("BOTS_FILE", str(ROOT / "bots.local.json"))
BOTS_EXAMPLE_FILE = _path_env("BOTS_EXAMPLE_FILE", str(ROOT / "bots.example.json"))
BOT_NAME_RE = re.compile(r"^[a-z0-9_]{4,25}$")


def _env_suffix(name: str) -> str:
    return re.sub(r"[^A-Z0-9_]+", "_", name.upper()).strip("_")


def _message_frequency(raw: Any) -> tuple[int, int]:
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        try:
            low, high = int(raw[0]), int(raw[1])
            if low > 0 and high >= low:
                return low, high
        except (TypeError, ValueError):
            pass
    return 45, 95


def _load_bot_payload(path: Path) -> list[Any]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict):
        payload = payload.get("bots", [])
    return payload if isinstance(payload, list) else []


def _bot_from_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("name") or entry.get("username") or "").strip().lower()
    if not BOT_NAME_RE.fullmatch(name):
        return None

    suffix = str(entry.get("env_suffix") or _env_suffix(name))
    username_env = str(entry.get("username_env") or f"TWITCH_BOT_USERNAME_{suffix}")
    token_env = str(entry.get("token_env") or f"TWITCH_BOT_TOKEN_{suffix}")
    username_default = str(entry.get("username") or name).strip().lower()
    persona = entry.get("persona") if isinstance(entry.get("persona"), dict) else {}

    return {
        "name": name,
        "username": _env(username_env, username_default),
        "token": _env(token_env),
        "message_frequency": _message_frequency(entry.get("message_frequency")),
        "is_moderator": bool(entry.get("is_moderator", False)),
        "persona": persona,
    }


def load_bots(path: Path | str | None = None) -> list[dict[str, Any]]:
    config_path = Path(path) if path else BOTS_FILE
    entries = _load_bot_payload(config_path)
    if not entries and config_path != BOTS_EXAMPLE_FILE:
        entries = _load_bot_payload(BOTS_EXAMPLE_FILE)

    bots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        bot = _bot_from_entry(entry)
        if not bot or bot["name"] in seen:
            continue
        bots.append(bot)
        seen.add(bot["name"])
    return bots


BOTS = load_bots()

OPENAI_API_KEY = _env("OPENAI_API_KEY")
OPENAI_MODEL = _env("OPENAI_MODEL", "gpt-4o-mini")
LLM_MAX_TOKENS = _int_env("LLM_MAX_TOKENS", 40)

AZURE_SPEECH_KEY = _env("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION = _env("AZURE_SPEECH_REGION", "westus")
TRANSCRIPT_HTTP_ENDPOINT = _env("TRANSCRIPT_HTTP_ENDPOINT", "http://127.0.0.1:5001/latest-transcript")
TRANSCRIPT_HTTP_TO_S = _float_env("TRANSCRIPT_HTTP_TO_S", 2.0)
MIC_MIN_WORDS = _int_env("MIC_MIN_WORDS", 2)
MIC_MIN_CHARS = _int_env("MIC_MIN_CHARS", 6)

SHARED_TRANSCRIPT_FILE = _env("SHARED_TRANSCRIPT_FILE", str(ROOT / "shared_transcript.json"))
CROSSBOT_RECENT_FILE = _env("CROSSBOT_RECENT_FILE", str(ROOT / "recent_messages.json"))
SHARED_META_FILE = _env("SHARED_META_FILE", str(ROOT / "shared_meta.json"))
HEARTBEAT_DIR = _env("HEARTBEAT_DIR", str(ROOT / "run" / "heartbeats"))
LOG_DIR = _env("LOG_DIR", str(ROOT / "run" / "logs"))

GAMEBANK_JSON = _env("GAMEBANK_JSON", str(ROOT / "gamebank.json"))
GAMEBANK_TXT = _env("GAMEBANK_TXT", str(ROOT / "gamebank.txt"))

HELIX_CACHE_FILE = _env("HELIX_CACHE_FILE", str(ROOT / "run" / "helix_cache.json"))
HELIX_CACHE_TTL_S = _int_env("HELIX_CACHE_TTL_S", 300)

MAX_MSG_PER_MINUTE = _int_env("MAX_MSG_PER_MINUTE", 6)
GLOBAL_MAX_MSG_PER_MINUTE = _int_env("GLOBAL_MAX_MSG_PER_MINUTE", 10)
GLOBAL_MIN_COOLDOWN_SECS = _float_env("GLOBAL_MIN_COOLDOWN_SECS", 2.0)
BASE_MIN_COOLDOWN_SECS = _int_env("BASE_MIN_COOLDOWN_SECS", 12)

REALISTIC_MESSAGES = {
    "hype": ["LETS GOOO", "POGGERS", "W", "NO SHOT", "HUGE", "GIGACHAD"],
    "fail": ["L", "rip", "F", "sadge", "KEKW", "oof", "unlucky"],
    "question": ["what build", "whats next", "pb soon", "plan for next run"],
    "casual": ["true", "facts", "real", "based", "same", "mood", "tbh"],
    "memes": ["skill issue", "clip it", "scripted", "built different"],
}

TYPING_PATTERNS = {
    "all_lowercase": 0.60,
    "all_caps": 0.10,
    "no_punctuation": 0.75,
}

FALLBACK_QUESTIONS = [
    "favorite part so far",
    "hardest section yet",
    "whats the plan next",
    "controller or mkb",
]


def validate_runtime(require_helix: bool = True) -> list[str]:
    errors: list[str] = []

    if not OPENAI_API_KEY:
        errors.append("Missing OPENAI_API_KEY")

    if not BOTS:
        errors.append("No bots configured; run quickstart.py or create bots.local.json")

    for bot in BOTS:
        if not bot["token"]:
            errors.append(f"Missing token for bot '{bot['name']}'")
        elif not is_valid_twitch_token(bot["token"]):
            errors.append(f"Malformed token for bot '{bot['name']}' (expected oauth: prefix)")

    if require_helix:
        if not TWITCH_CLIENT_ID:
            errors.append("Missing TWITCH_CLIENT_ID")
        if not TWITCH_CLIENT_SECRET:
            errors.append("Missing TWITCH_CLIENT_SECRET")

    return errors
