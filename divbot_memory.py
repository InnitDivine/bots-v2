import time
from pathlib import Path
from typing import Any

from config import DIVBOTS_MEMORY_FILE
from divbot_policy import norm_message
from shared import _atomic_write_json, _file_lock, _read_json


class DivBotMemory:
    def __init__(self, path: str | Path = DIVBOTS_MEMORY_FILE):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _payload(self) -> dict[str, Any]:
        data = _read_json(self.path, {"blocked_phrases": [], "forgotten_topics": []})
        if not isinstance(data, dict):
            return {"blocked_phrases": [], "forgotten_topics": []}
        data.setdefault("blocked_phrases", [])
        data.setdefault("forgotten_topics", [])
        return data

    def snapshot(self) -> dict[str, Any]:
        with _file_lock(self.path):
            data = self._payload()
        return {
            "blocked_phrases": list(data.get("blocked_phrases", [])),
            "forgotten_topics": list(data.get("forgotten_topics", [])),
            "ts": data.get("ts"),
        }

    def blocked_phrases(self) -> list[str]:
        return [str(x) for x in self.snapshot().get("blocked_phrases", []) if str(x).strip()]

    def is_blocked(self, text: str) -> bool:
        norm = norm_message(text)
        return any(norm_message(phrase) in norm for phrase in self.blocked_phrases() if norm_message(phrase))

    def block_phrase(self, phrase: str) -> bool:
        clean = " ".join((phrase or "").split())
        if not clean:
            return False
        with _file_lock(self.path):
            data = self._payload()
            phrases = [str(x) for x in data.get("blocked_phrases", [])]
            norms = {norm_message(x) for x in phrases}
            if norm_message(clean) not in norms:
                phrases.append(clean)
            data["blocked_phrases"] = phrases[-500:]
            data["ts"] = time.time()
            _atomic_write_json(self.path, data)
        return True

    def forget_topic(self, topic: str) -> bool:
        clean = " ".join((topic or "").split())
        if not clean:
            return False
        with _file_lock(self.path):
            data = self._payload()
            topics = [str(x) for x in data.get("forgotten_topics", [])]
            norms = {norm_message(x) for x in topics}
            if norm_message(clean) not in norms:
                topics.append(clean)
            data["forgotten_topics"] = topics[-500:]
            data["ts"] = time.time()
            _atomic_write_json(self.path, data)
        self.block_phrase(clean)
        return True

    def avoid_text(self) -> str:
        snap = self.snapshot()
        terms = [*snap.get("blocked_phrases", []), *snap.get("forgotten_topics", [])]
        terms = [str(x).strip() for x in terms if str(x).strip()]
        return ", ".join(terms[:25]) if terms else "none"
