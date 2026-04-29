import os
import re
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
TOKEN_PREFIX = "oauth:"
_TOKEN_BODY_RE = re.compile(r"^[^\s:]+$")


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
    if not body or not _TOKEN_BODY_RE.fullmatch(body):
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

TARGET_CHANNEL = _env("TARGET_CHANNEL", "innitdivine")

TWITCH_CLIENT_ID = _env("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = _env("TWITCH_CLIENT_SECRET")

BOTS = [
    {
        "name": "sienna",
        "username": _env("TWITCH_BOT_USERNAME_SIENNA", "example_chillbot"),
        "token": _env("TWITCH_BOT_TOKEN_SIENNA"),
        "message_frequency": (35, 80),
        "is_moderator": False,
    },
    {
        "name": "knight",
        "username": _env("TWITCH_BOT_USERNAME_KNIGHT", "example_modbot"),
        "token": _env("TWITCH_BOT_TOKEN_KNIGHT"),
        "message_frequency": (40, 90),
        "is_moderator": True,
    },
    {
        "name": "simp",
        "username": _env("TWITCH_BOT_USERNAME_SIMP", "example_hypebot"),
        "token": _env("TWITCH_BOT_TOKEN_SIMP"),
        "message_frequency": (30, 70),
        "is_moderator": False,
    },
]

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
