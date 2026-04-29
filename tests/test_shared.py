import json
from concurrent.futures import ThreadPoolExecutor

from shared import SharedState


def _state(tmp_path):
    return SharedState(str(tmp_path / "shared.json"), str(tmp_path / "recent.json"), str(tmp_path / "meta.json"))


def test_global_dedupe(tmp_path):
    s = _state(tmp_path)
    s.remember_global_message("sienna", "LETS GOOO")
    assert s.is_global_duplicate("lets gooo") is True
    assert s.is_global_duplicate("new line") is False


def test_corrupted_recent_json_is_recovered(tmp_path):
    s = _state(tmp_path)
    s.recent_messages_path.write_text("{broken", encoding="utf-8")

    assert s.is_global_duplicate("safe line") is False
    data = json.loads(s.recent_messages_path.read_text(encoding="utf-8"))
    assert data == {"messages": []}


def test_concurrent_recent_writes_are_merged(tmp_path):
    s = _state(tmp_path)

    def write(i):
        local = _state(tmp_path)
        return local.try_remember_global_message("bot", f"line {i}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, range(30)))

    data = json.loads(s.recent_messages_path.read_text(encoding="utf-8"))
    assert all(results)
    assert len(data["messages"]) == 30
    assert {row["norm"] for row in data["messages"]} == {f"line {i}" for i in range(30)}


def test_try_remember_rejects_duplicates_and_global_throttle(tmp_path):
    s = _state(tmp_path)
    assert s.try_remember_global_message("sienna", "same line", max_per_minute=2)
    assert not s.try_remember_global_message("knight", "same line", max_per_minute=2)
    assert s.try_remember_global_message("simp", "another line", max_per_minute=2)
    assert not s.try_remember_global_message("sienna", "third line", max_per_minute=2)


def test_ttl_pruning(tmp_path):
    s = _state(tmp_path)
    s.recent_messages_path.write_text(
        json.dumps(
            {
                "messages": [
                    {"bot": "old", "norm": "old", "raw": "old", "ts": 1.0},
                    {"bot": "new", "norm": "new", "raw": "new", "ts": 9999999999.0},
                ]
            }
        ),
        encoding="utf-8",
    )

    s.prune_recent_messages(ttl_s=120.0)
    data = json.loads(s.recent_messages_path.read_text(encoding="utf-8"))
    assert [row["norm"] for row in data["messages"]] == ["new"]


def test_transcript_updates_meta_signals(tmp_path):
    writer = _state(tmp_path)
    reader = _state(tmp_path)

    writer.on_text("LETS GO what build now?", final=True)
    reader.refresh_meta()

    assert reader.hype == 9
    assert reader.detected_emotion == "hype"
    assert reader.help_mode is True
