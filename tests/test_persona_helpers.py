from collections import deque
from types import SimpleNamespace

from bot_persona import Bot, MAX_WORDS, _swap_streamer_alias
from config import FALLBACK_QUESTIONS, MAX_MSG_PER_MINUTE


class _DummyRng:
    def __init__(self):
        self.i = 0

    def random(self):
        self.i += 1
        return 0.1

    def choice(self, arr):
        return arr[0]


def test_alias_swap():
    rng = _DummyRng()
    out = _swap_streamer_alias(rng, "the streamer is cracked")
    assert "streamer" not in out.lower()


def test_recent_hint_format():
    d = deque(maxlen=8)
    d.append("line one")
    d.append("line two")
    assert " | ".join(list(d)[-6:]) == "line one | line two"


class _FakeShared:
    game = "Game"
    global_emotes = ["Kappa"]
    silence = 0.0
    hype = 0

    def is_global_duplicate(self, message):
        return False


class _FakeGB:
    def __init__(self, bank=None):
        self.bank = bank or {}

    def get(self, game):
        return self.bank.get(game)


def _bot(bank=None):
    bot = object.__new__(Bot)
    bot.rng = _DummyRng()
    bot.shared = _FakeShared()
    bot.gb = _FakeGB(bank)
    bot.p = SimpleNamespace(name="sienna", username="sienna")
    bot._last_sent_at = 0.0
    bot._sent_window = deque(maxlen=MAX_MSG_PER_MINUTE * 2)
    bot._recent_local_lines = deque(maxlen=8)
    return bot


def test_cooldown_and_rate_limit_are_per_bot():
    first = _bot()
    second = _bot()

    first._mark_sent()

    assert first._cooldown_ok() is False
    assert second._cooldown_ok() is True

    for _ in range(MAX_MSG_PER_MINUTE - 1):
        first._mark_sent()
    assert first._rate_limit_ok() is False


def test_sanitize_blocks_banned_terms_ascii_and_max_words():
    bot = _bot({"Game": {"ban_terms": ["spoiler"]}})

    assert bot._sanitize("hello \U0001f600") == "hello"
    assert bot._sanitize("this has spoiler") is None

    long_msg = " ".join(f"w{i}" for i in range(MAX_WORDS + 5))
    assert len(bot._sanitize(long_msg).split()) == MAX_WORDS


def test_duplicate_prevention_chooses_alternate_message():
    bot = _bot()
    bot._recent_local_lines.append("same line")

    assert bot._diversify("same line", ctx="general", help_mode=False) != "same line"


def test_game_question_uses_fallback_messages():
    assert _bot()._game_question() in FALLBACK_QUESTIONS
