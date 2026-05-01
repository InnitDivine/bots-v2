import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

SEND_MODES = {
    "idle_question",
    "hype_reaction",
    "fail_reaction",
    "streamer_followup",
    "game_question",
    "emote_only",
    "chat_reply",
}


def truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def norm_message(text: str) -> str:
    text = (text or "").lower().strip()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class PolicyConfig:
    real_chat_suppression_seconds: float = 20.0
    max_cast_messages_per_5_min: int = 18
    idle_only: bool = False
    ascii_only: bool = True
    allow_bot_to_bot: bool = False
    hype_fail_exception_limit: int = 2
    transcript_trigger_limit: int = 3

    @classmethod
    def from_env(cls) -> "PolicyConfig":
        return cls(
            real_chat_suppression_seconds=_float_env("DIVBOTS_REAL_CHAT_SUPPRESSION_SECONDS", 20.0),
            max_cast_messages_per_5_min=_int_env("DIVBOTS_MAX_CAST_MESSAGES_PER_5_MIN", 18),
            idle_only=truthy(os.getenv("DIVBOTS_IDLE_ONLY")),
            ascii_only=truthy(os.getenv("DIVBOTS_ASCII_ONLY", "true")),
            allow_bot_to_bot=truthy(os.getenv("DIVBOTS_ALLOW_BOT_TO_BOT")),
        )


@dataclass
class BotPolicyState:
    last_sent_at: float = 0.0
    sent_window: deque[float] = field(default_factory=deque)
    cast_window: deque[float] = field(default_factory=deque)
    transcript_window: deque[float] = field(default_factory=deque)
    hype_fail_window: deque[float] = field(default_factory=deque)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str = "allowed"


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def prune_window(values: deque[float], now: float, seconds: float) -> None:
    while values and now - values[0] > seconds:
        values.popleft()


def contains_blocked_phrase(message: str, blocked_phrases: Iterable[str]) -> bool:
    norm = norm_message(message)
    return any(norm_message(phrase) in norm for phrase in blocked_phrases if norm_message(phrase))


def sanitize_message(message: str, max_words: int = 12, ascii_only: bool = True) -> str:
    clean = " ".join((message or "").split())
    if ascii_only:
        clean = clean.encode("ascii", "ignore").decode("ascii")
    words = clean.split()
    if max_words > 0 and len(words) > max_words:
        clean = " ".join(words[:max_words])
    return clean.strip()


def decide_send(
    config: PolicyConfig,
    state: BotPolicyState,
    *,
    mode: str,
    now: float | None = None,
    bot_cooldown_s: float = 0.0,
    per_bot_max_per_minute: int = 6,
    last_real_chat_at: float = 0.0,
    is_duplicate: bool = False,
    is_bot_author: bool = False,
) -> PolicyDecision:
    now = time.time() if now is None else now
    prune_window(state.sent_window, now, 60.0)
    prune_window(state.cast_window, now, 300.0)
    prune_window(state.transcript_window, now, 60.0)
    prune_window(state.hype_fail_window, now, 60.0)

    if mode not in SEND_MODES:
        return PolicyDecision(False, f"unknown mode: {mode}")
    if config.idle_only and now - last_real_chat_at < config.real_chat_suppression_seconds:
        return PolicyDecision(False, "idle_only real chat suppression")
    if now - last_real_chat_at < config.real_chat_suppression_seconds and mode not in {"chat_reply"}:
        return PolicyDecision(False, "recent real chat")
    if is_bot_author and not config.allow_bot_to_bot:
        return PolicyDecision(False, "bot-to-bot disabled")
    if is_duplicate:
        return PolicyDecision(False, "duplicate")
    if now - state.last_sent_at < bot_cooldown_s:
        return PolicyDecision(False, "bot cooldown")
    if len(state.sent_window) >= per_bot_max_per_minute:
        return PolicyDecision(False, "bot per-minute limit")
    if len(state.cast_window) >= config.max_cast_messages_per_5_min:
        return PolicyDecision(False, "cast 5-minute limit")
    if mode in {"hype_reaction", "fail_reaction"} and len(state.hype_fail_window) >= config.hype_fail_exception_limit:
        return PolicyDecision(False, "hype/fail exception limit")
    if mode in {"streamer_followup", "game_question"} and len(state.transcript_window) >= config.transcript_trigger_limit:
        return PolicyDecision(False, "transcript trigger limit")
    return PolicyDecision(True)


def mark_send(state: BotPolicyState, mode: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    state.last_sent_at = now
    state.sent_window.append(now)
    state.cast_window.append(now)
    if mode in {"streamer_followup", "game_question"}:
        state.transcript_window.append(now)
    if mode in {"hype_reaction", "fail_reaction"}:
        state.hype_fail_window.append(now)
