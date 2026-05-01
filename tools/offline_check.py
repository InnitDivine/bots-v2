import importlib
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import BOTS, is_valid_twitch_token, normalize_twitch_token, validate_bot_configs
from divbot_commands import apply_divbots_command, parse_divbots_command
from divbot_memory import DivBotMemory
from divbot_policy import BotPolicyState, PolicyConfig, decide_send, mark_send, sanitize_message
from shared import SharedState, _read_json


def check_config() -> None:
    assert BOTS, "expected example bots"
    errors = validate_bot_configs(BOTS)
    assert not errors, f"bot config errors: {errors}"


def check_tokens() -> None:
    assert normalize_twitch_token("oauth:abc1234") == "oauth:abc1234"
    assert normalize_twitch_token("Bearer oauth:abc1234") == "oauth:abc1234"
    assert normalize_twitch_token("") == ""  # empty handled by caller as missing
    assert is_valid_twitch_token("oauth:abc1234")
    assert not is_valid_twitch_token("abc1234")
    assert not is_valid_twitch_token("")
    for raw in ("abc1234", "oauth:...", "oauth:changeme", "oauth:"):
        try:
            normalize_twitch_token(raw)
        except ValueError:
            pass
        else:
            raise AssertionError(f"normalize_twitch_token accepted invalid: {raw!r}")


def check_shared_json() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        shared = SharedState(
            str(root / "shared_transcript.json"),
            str(root / "recent_messages.json"),
            str(root / "shared_meta.json"),
        )
        shared.on_text("lets go huge play", final=True)
        shared.set_game("Example Game")
        assert shared.try_remember_global_message("example", "hello chat")
        assert not shared.try_remember_global_message("example", "hello chat")


def check_corrupt_json_recovery() -> None:
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "broken.json"
        target.write_text("{this is not json", encoding="utf-8")
        result = _read_json(target, {"messages": []})
        assert result == {"messages": []}, f"expected fallback, got {result!r}"

        shared = SharedState(
            str(Path(td) / "transcript.json"),
            str(target),
            str(Path(td) / "meta.json"),
        )
        # is_global_duplicate should not crash on corrupt recent file
        assert shared.is_global_duplicate("anything") is False


def check_ttl_prune() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        shared = SharedState(
            str(root / "transcript.json"),
            str(root / "recent.json"),
            str(root / "meta.json"),
        )
        # Reserve a unique message, then prune with a tiny TTL so it ages out.
        assert shared.try_remember_global_message("bot", "ping unique line", ttl_s=0.0)
        shared.prune_recent_messages(ttl_s=0.0)
        # Same line is no longer a duplicate after TTL prune.
        assert shared.try_remember_global_message("bot", "ping unique line", ttl_s=0.0)


def check_policy() -> None:
    state = BotPolicyState()
    cfg = PolicyConfig(real_chat_suppression_seconds=10, max_cast_messages_per_5_min=2)
    assert not decide_send(cfg, state, mode="idle_question", now=20, last_real_chat_at=15).allowed
    assert decide_send(cfg, state, mode="chat_reply", now=20, last_real_chat_at=15).allowed
    mark_send(state, "chat_reply", now=20)
    mark_send(state, "chat_reply", now=21)
    assert not decide_send(cfg, state, mode="chat_reply", now=22, last_real_chat_at=0).allowed


def check_sanitize() -> None:
    msg = sanitize_message("hello ✨ this is a very long test message for chat", max_words=5, ascii_only=True)
    assert msg == "hello this is a very"


def check_blocked_phrase_memory() -> None:
    with tempfile.TemporaryDirectory() as td:
        memory = DivBotMemory(path=Path(td) / "divbot_memory.json")
        assert memory.blocked_phrases() == []
        assert memory.block_phrase("forbidden topic")
        # Second call is a no-op; same phrase only stored once.
        assert memory.block_phrase("forbidden topic")
        phrases = memory.blocked_phrases()
        assert phrases == ["forbidden topic"], phrases
        assert memory.is_blocked("you mentioned the forbidden topic again")
        assert not memory.is_blocked("totally fine line")
        # forget_topic also blocks the phrase.
        memory.forget_topic("another bad subject")
        assert memory.is_blocked("we agreed on another bad subject")


def check_command_parsing() -> None:
    assert parse_divbots_command("hello chat") is None
    assert parse_divbots_command("!divbots") == ("status", "")
    assert parse_divbots_command("!divbots quiet 5m") == ("quiet", "5m")
    assert parse_divbots_command("!divbots block bad phrase") == ("block", "bad phrase")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        shared = SharedState(
            str(root / "transcript.json"),
            str(root / "recent.json"),
            str(root / "meta.json"),
        )
        memory = DivBotMemory(path=root / "memory.json")
        # Unrelated message must not be flagged as handled.
        result = apply_divbots_command("hi", shared, memory)
        assert not result.handled
        # Status command always returns a response without secrets.
        result = apply_divbots_command("!divbots", shared, memory)
        assert result.handled and "DivBots status" in result.response


def check_launch_status_import() -> None:
    # Smoke-import the launch and status modules to catch import-time wiring errors.
    for module in ("launch_multi", "status"):
        importlib.import_module(module)


def main() -> None:
    checks = [
        ("config parsing", check_config),
        ("token validation", check_tokens),
        ("shared JSON", check_shared_json),
        ("corrupt JSON recovery", check_corrupt_json_recovery),
        ("TTL prune", check_ttl_prune),
        ("policy throttle", check_policy),
        ("sanitize/max words", check_sanitize),
        ("blocked phrase memory", check_blocked_phrase_memory),
        ("command parsing", check_command_parsing),
        ("launch/status import", check_launch_status_import),
    ]
    for name, fn in checks:
        fn()
        print(f"ok - {name}")
    print("offline checks passed")


if __name__ == "__main__":
    main()
