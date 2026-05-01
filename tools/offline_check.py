import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import BOTS, is_valid_twitch_token, normalize_twitch_token, validate_bot_configs
from divbot_policy import BotPolicyState, PolicyConfig, decide_send, mark_send, sanitize_message
from shared import SharedState


def check_config() -> None:
    assert BOTS, "expected example bots"
    errors = validate_bot_configs(BOTS)
    assert not errors, f"bot config errors: {errors}"


def check_tokens() -> None:
    assert normalize_twitch_token("abc") == "oauth:abc"
    assert is_valid_twitch_token("oauth:abc")
    assert not is_valid_twitch_token("abc")
    try:
        normalize_twitch_token("oauth:...")
    except ValueError:
        pass
    else:
        raise AssertionError("placeholder token accepted")


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


def main() -> None:
    checks = [
        ("config parsing", check_config),
        ("token validation", check_tokens),
        ("shared JSON", check_shared_json),
        ("policy throttle", check_policy),
        ("sanitize/max words", check_sanitize),
    ]
    for name, fn in checks:
        fn()
        print(f"ok - {name}")
    print("offline checks passed")


if __name__ == "__main__":
    main()
