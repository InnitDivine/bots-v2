import argparse
import time
import zlib
from pathlib import Path
from typing import Any

from config import (
    BOTS,
    CROSSBOT_RECENT_FILE,
    DIVBOTS_ORCHESTRATOR_HINT_TTL_SECS,
    DIVBOTS_ORCHESTRATOR_IDLE_SILENCE_SECS,
    DIVBOTS_ORCHESTRATOR_TICK_SECS,
    SHARED_META_FILE,
    SHARED_TRANSCRIPT_FILE,
)
from shared import SharedState, _file_lock, _read_json

TRANSCRIPT_MODES = {"streamer_followup", "game_question", "hype_reaction", "fail_reaction"}


def _locked_json(path: str | Path, fallback: Any) -> Any:
    target = Path(path)
    with _file_lock(target):
        return _read_json(target, fallback)


def _active_hint(meta: dict[str, Any], now: float) -> dict[str, Any] | None:
    hint = meta.get("next_speaker")
    if not isinstance(hint, dict):
        return None
    try:
        if now < float(hint.get("expires_at", 0)):
            return hint
    except (TypeError, ValueError):
        return None
    return None


def _mode_allowed(bot: dict[str, Any], mode: str) -> bool:
    modes = bot.get("message_modes") or []
    if mode not in modes:
        return False
    if mode in {"idle_question", "game_question"} and not bot.get("can_prompt_streamer", True):
        return False
    if mode in TRANSCRIPT_MODES and not bot.get("can_react_to_transcript", True):
        return False
    if mode == "chat_reply" and not bot.get("can_react_to_chat", True):
        return False
    if mode == "emote_only" and not bot.get("can_use_emotes", True):
        return False
    return True


def _recent_scores(now: float) -> dict[str, tuple[int, float]]:
    data = _locked_json(CROSSBOT_RECENT_FILE, {"messages": []})
    scores: dict[str, tuple[int, float]] = {}
    if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
        return scores
    counts: dict[str, int] = {}
    latest: dict[str, float] = {}
    for row in data["messages"]:
        if not isinstance(row, dict):
            continue
        bot = str(row.get("bot") or "").strip().lower()
        if not bot:
            continue
        try:
            ts = float(row.get("ts", 0))
        except (TypeError, ValueError):
            continue
        if now - ts <= 300.0:
            counts[bot] = counts.get(bot, 0) + 1
        latest[bot] = max(latest.get(bot, 0.0), ts)
    for bot in set(counts) | set(latest):
        scores[bot] = (counts.get(bot, 0), latest.get(bot, 0.0))
    return scores


def choose_bot_for_mode(bots: list[dict[str, Any]], mode: str, source_id: str, now: float) -> str | None:
    eligible = [bot for bot in bots if _mode_allowed(bot, mode)]
    if not eligible:
        return None
    scores = _recent_scores(now)
    eligible.sort(key=lambda bot: (*scores.get(bot["name"], (0, 0.0)), bot["name"]))
    best_score = scores.get(eligible[0]["name"], (0, 0.0))
    tied = [bot for bot in eligible if scores.get(bot["name"], (0, 0.0)) == best_score]
    pick_index = zlib.crc32(source_id.encode("utf-8", "ignore")) % len(tied)
    return tied[pick_index]["name"]


def _quiet_active(meta: dict[str, Any], now: float) -> bool:
    if bool(meta.get("stopped")):
        return True
    try:
        return now < float(meta.get("quiet_until", 0))
    except (TypeError, ValueError):
        return False


def _float_meta(payload: dict[str, Any], key: str) -> float:
    try:
        return float(payload.get(key, 0))
    except (TypeError, ValueError):
        return 0.0


def _chat_mode(meta: dict[str, Any], now: float) -> tuple[str, str, str] | None:
    chat_at = _float_meta(meta, "last_real_chat_at")
    chat_login = str(meta.get("last_real_chat_login") or "").strip().lower()
    if chat_login and now - chat_at <= 12.0:
        return "chat_reply", f"chat:{chat_login}:{int(chat_at)}", "real_chat"
    return None


def _transcript_mode(meta: dict[str, Any], transcript: dict[str, Any], now: float) -> tuple[str, str, str] | None:
    source_id = str(transcript.get("id") or "").strip()
    transcript_text = str(transcript.get("text") or "").strip()
    transcript_at = _float_meta(transcript, "ts")
    if not (source_id and transcript_text and now - transcript_at <= 90.0):
        return None
    emotion = str(meta.get("detected_emotion") or "").lower()
    if emotion == "hype":
        return "hype_reaction", source_id, "transcript_hype"
    if emotion == "fail":
        return "fail_reaction", source_id, "transcript_fail"
    if bool(meta.get("help_mode")):
        return "streamer_followup", source_id, "transcript_help"
    if "?" in transcript_text:
        return "game_question", source_id, "transcript_question"
    return "streamer_followup", source_id, "transcript"


def _hype_mode(meta: dict[str, Any], now: float) -> tuple[str, str, str] | None:
    try:
        hype = int(meta.get("hype", 0))
    except (TypeError, ValueError):
        hype = 0
    if hype > 7:
        return "emote_only", f"hype:{int(now // 10)}", "hype"
    return None


def _idle_mode(meta: dict[str, Any], transcript: dict[str, Any], now: float, idle_s: float) -> tuple[str, str, str] | None:
    last_activity = max(_float_meta(meta, "last_real_chat_at"), _float_meta(transcript, "ts"))
    if last_activity > 0 and now - last_activity >= idle_s:
        return "idle_question", f"idle:{int(now // max(1.0, idle_s))}", "idle"
    return None


def choose_mode(meta: dict[str, Any], transcript: dict[str, Any], now: float, idle_s: float) -> tuple[str, str, str] | None:
    if _quiet_active(meta, now):
        return None
    for chooser in (
        lambda: _chat_mode(meta, now),
        lambda: _transcript_mode(meta, transcript, now),
        lambda: _hype_mode(meta, now),
        lambda: _idle_mode(meta, transcript, now, idle_s),
    ):
        choice = chooser()
        if choice:
            return choice
    return None


def orchestrate_once(shared: SharedState, last_source_id: str = "") -> str:
    now = time.time()
    meta = _locked_json(SHARED_META_FILE, {})
    transcript = _locked_json(SHARED_TRANSCRIPT_FILE, {})
    if not isinstance(meta, dict):
        meta = {}
    if not isinstance(transcript, dict):
        transcript = {}
    if _active_hint(meta, now):
        return last_source_id

    chosen = choose_mode(meta, transcript, now, DIVBOTS_ORCHESTRATOR_IDLE_SILENCE_SECS)
    if not chosen:
        return last_source_id
    mode, source_id, reason = chosen
    if source_id == last_source_id:
        return last_source_id

    bot_name = choose_bot_for_mode(BOTS, mode, source_id, now)
    if not bot_name:
        return last_source_id
    shared.set_next_speaker_hint(
        bot_name,
        mode,
        reason=reason,
        source_id=source_id,
        ttl_s=DIVBOTS_ORCHESTRATOR_HINT_TTL_SECS,
    )
    print(f"orchestrator -> {bot_name} {mode} ({reason})")
    return source_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Choose one DivBots cast member per shared-state tick.")
    parser.add_argument("--once", action="store_true", help="Run one planning tick and exit")
    parser.add_argument("--tick", type=float, default=DIVBOTS_ORCHESTRATOR_TICK_SECS)
    args = parser.parse_args()

    shared = SharedState(SHARED_TRANSCRIPT_FILE, CROSSBOT_RECENT_FILE, SHARED_META_FILE)
    last_source_id = ""
    while True:
        last_source_id = orchestrate_once(shared, last_source_id)
        if args.once:
            return
        time.sleep(max(0.5, args.tick))


if __name__ == "__main__":
    main()
