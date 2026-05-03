import contextlib
import json
import os
import re
import threading
import time
import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows path
    fcntl = None

try:
    import msvcrt  # type: ignore
except ImportError:  # pragma: no cover - POSIX path
    msvcrt = None

LOCK_TIMEOUT_S = 5.0
LOCK_POLL_S = 0.025
_INPROC_LOCKS: dict[str, threading.RLock] = {}
_INPROC_LOCKS_GUARD = threading.Lock()

HYPE_TERMS = {"lets go", "let s go", "pog", "poggers", "huge", "clutch", "gg", "w"}
FAIL_TERMS = {"rip", "unlucky", "oof", "fail", "lost", "dead", "bad run", "throw"}
HELP_TERMS = {"help", "how", "what", "why", "where", "when", "which", "plan", "build"}


def _h12(text: str) -> str:
    raw = (text or "").encode("utf-8", "ignore")
    return f"{zlib.crc32(raw):08x}{len(raw) & 0xFFFF:04x}"


def _norm_msg(msg: str) -> str:
    msg = (msg or "").lower().strip()
    msg = re.sub(r"[^a-z0-9]+", " ", msg)
    return re.sub(r"\s+", " ", msg).strip()


def _fallback_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


@contextmanager
def _file_lock(target: Path) -> Iterator[None]:
    lock_path = target.with_suffix(target.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    key = str(lock_path.resolve())
    with _INPROC_LOCKS_GUARD:
        inproc_lock = _INPROC_LOCKS.setdefault(key, threading.RLock())
    inproc_lock.acquire()
    try:
        with lock_path.open("a+b") as handle:
            handle.seek(0)
            handle.write(b"\0")
            handle.flush()
            handle.seek(0)
            start = time.time()
            locked = False
            while not locked:
                try:
                    if msvcrt is not None:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    elif fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except OSError:
                    if time.time() - start >= LOCK_TIMEOUT_S:
                        raise TimeoutError(f"Timed out waiting for lock: {target.name}")
                    time.sleep(LOCK_POLL_S)
            try:
                yield
            finally:
                handle.seek(0)
                if msvcrt is not None:
                    with contextlib.suppress(OSError):
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                elif fcntl is not None:
                    with contextlib.suppress(OSError):
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        inproc_lock.release()


def _read_json(path: Path, fallback: Any) -> Any:
    for _ in range(3):
        if not path.exists():
            return _fallback_copy(fallback)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            time.sleep(0.02)
        except OSError:
            time.sleep(0.02)
    return _fallback_copy(fallback)


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


def _prune_messages(messages: list[Any], now: float, ttl_s: float) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for row in messages:
        if not isinstance(row, dict):
            continue
        try:
            ts = float(row.get("ts", 0))
        except (TypeError, ValueError):
            continue
        if now - ts <= ttl_s:
            kept.append(row)
    return kept


def _text_signals(text: str) -> tuple[int, str, bool]:
    norm = _norm_msg(text)
    words = set(norm.split())
    has_hype = any(term in norm for term in HYPE_TERMS)
    has_fail = any(term in norm for term in FAIL_TERMS)
    help_mode = "?" in text or bool(words & HELP_TERMS)
    if has_hype:
        return 9, "hype", help_mode
    if has_fail:
        return 2, "fail", help_mode
    return 0, "neutral", help_mode


class SharedState:
    """Per-process state with cross-process transcript + dedupe files."""

    def __init__(self, transcript_path: str, recent_messages_path: str, meta_path: str | None = None):
        self.transcript_path = Path(transcript_path)
        self.recent_messages_path = Path(recent_messages_path)
        self.meta_path = Path(meta_path) if meta_path else None
        self.transcript_path.parent.mkdir(parents=True, exist_ok=True)
        self.recent_messages_path.parent.mkdir(parents=True, exist_ok=True)
        if self.meta_path is not None:
            self.meta_path.parent.mkdir(parents=True, exist_ok=True)

        self.game: str = ""
        self.global_emotes: list[str] = []
        self.hype: int = 0
        self.detected_emotion: str = "neutral"
        self.help_mode: bool = False
        self.last_real_chat_at: float = 0.0
        self.last_real_chat_login: str = ""
        self.last_real_chat_display: str = ""
        self.quiet_until: float = 0.0
        self.stopped: bool = False
        self.manual_topic: str = ""
        self.next_speaker_hint: dict[str, Any] | None = None

        self.silence: float = 0.0
        self.latest_text: str = ""
        self.latest_id: str = ""

    def tick(self, dt: float) -> None:
        self.silence += max(0.0, float(dt))

    def set_game(self, name: str) -> None:
        self.game = (name or "").strip()
        self._merge_meta({"game": self.game})

    def set_global_emotes(self, names: list[str]) -> None:
        self.global_emotes = names or []
        self._merge_meta({"global_emotes": self.global_emotes})

    def mark_real_chat(self, login: str = "", display_name: str = "") -> None:
        self.last_real_chat_at = time.time()
        self.last_real_chat_login = _norm_msg(login).replace(" ", "_")[:25]
        self.last_real_chat_display = (display_name or login or "").strip()[:25]
        updates: dict[str, Any] = {"last_real_chat_at": self.last_real_chat_at}
        if self.last_real_chat_login:
            updates["last_real_chat_login"] = self.last_real_chat_login
        if self.last_real_chat_display:
            updates["last_real_chat_display"] = self.last_real_chat_display
        self._merge_meta(updates)

    def set_quiet(self, seconds: float = 300.0) -> None:
        self.quiet_until = time.time() + max(1.0, float(seconds))
        self.stopped = False
        self._merge_meta({"quiet_until": self.quiet_until, "stopped": False})

    def resume(self) -> None:
        self.quiet_until = 0.0
        self.stopped = False
        self._merge_meta({"quiet_until": 0.0, "stopped": False})

    def stop_cast(self) -> None:
        self.stopped = True
        self._merge_meta({"stopped": True})

    def set_topic(self, topic: str) -> None:
        self.manual_topic = (topic or "").strip()[:140]
        self._merge_meta({"manual_topic": self.manual_topic})

    def boost_hype(self) -> None:
        self.hype = 9
        self.detected_emotion = "hype"
        self._merge_meta({"hype": self.hype, "detected_emotion": self.detected_emotion})

    def is_quiet(self) -> bool:
        return self.stopped or time.time() < self.quiet_until

    def set_next_speaker_hint(
        self,
        bot_name: str,
        mode: str,
        *,
        reason: str = "",
        source_id: str = "",
        ttl_s: float = 12.0,
    ) -> dict[str, Any]:
        now = time.time()
        hint = {
            "id": _h12(f"{bot_name}:{mode}:{source_id}:{now}"),
            "bot": (bot_name or "").strip().lower(),
            "mode": (mode or "").strip(),
            "reason": (reason or "").strip()[:40],
            "source_id": (source_id or "").strip()[:80],
            "ts": now,
            "expires_at": now + max(1.0, float(ttl_s)),
        }
        self.next_speaker_hint = hint
        self._merge_meta({"next_speaker": hint})
        return hint

    def clear_next_speaker_hint(self, hint_id: str | None = None) -> None:
        if hint_id and self.next_speaker_hint and self.next_speaker_hint.get("id") != hint_id:
            return
        self.next_speaker_hint = None
        self._merge_meta({"next_speaker": None})

    def active_next_speaker_hint(self, now: float | None = None) -> dict[str, Any] | None:
        hint = self.next_speaker_hint
        if not isinstance(hint, dict):
            return None
        try:
            expires_at = float(hint.get("expires_at", 0))
        except (TypeError, ValueError):
            return None
        if (time.time() if now is None else now) >= expires_at:
            return None
        if not str(hint.get("bot") or "").strip() or not str(hint.get("mode") or "").strip():
            return None
        return hint

    def next_speaker_for(self, bot_name: str, now: float | None = None) -> dict[str, Any] | None:
        hint = self.active_next_speaker_hint(now)
        if hint and str(hint.get("bot") or "").lower() == (bot_name or "").lower():
            return hint
        return None

    def on_text(self, text: str, final: bool = True) -> None:
        if not final:
            return
        text = (text or "").strip()
        if not text:
            return

        line_id = _h12(text + str(int(time.time() * 1000)))
        hype, emotion, help_mode = _text_signals(text)
        self.hype = hype if hype else max(0, self.hype - 1)
        self.detected_emotion = emotion
        self.help_mode = help_mode
        self.latest_text = text
        self.latest_id = line_id
        self.silence = 0.0
        self._write_transcript(text, line_id)
        self._merge_meta(
            {
                "hype": self.hype,
                "detected_emotion": self.detected_emotion,
                "help_mode": self.help_mode,
            }
        )

    def _write_transcript(self, text: str, line_id: str) -> None:
        payload: dict[str, Any] = {
            "id": line_id,
            "text": text,
            "ts": time.time(),
        }
        with _file_lock(self.transcript_path):
            _atomic_write_json(self.transcript_path, payload)

    def refresh_transcript(self) -> None:
        with _file_lock(self.transcript_path):
            data = _read_json(self.transcript_path, {})
        if not isinstance(data, dict):
            return

        line_id = (data.get("id") or "").strip()
        text = (data.get("text") or "").strip()
        if not line_id or not text:
            return
        if line_id == self.latest_id:
            return

        self.latest_id = line_id
        self.latest_text = text
        self.silence = 0.0

    def _merge_meta(self, updates: dict[str, Any]) -> None:
        if self.meta_path is None:
            return
        payload = {**updates, "ts": time.time()}
        with _file_lock(self.meta_path):
            existing = _read_json(self.meta_path, {})
            if not isinstance(existing, dict):
                existing = {}
            existing.update(payload)
            _atomic_write_json(self.meta_path, existing)

    def _apply_meta_game(self, data: dict[str, Any]) -> None:
        game = (data.get("game") or "").strip()
        emotes = data.get("global_emotes")
        if game:
            self.game = game
        if isinstance(emotes, list):
            self.global_emotes = [str(x) for x in emotes if str(x).strip()]

    def _apply_meta_signal(self, data: dict[str, Any]) -> None:
        if "hype" in data:
            try:
                self.hype = max(0, min(10, int(data["hype"])))
            except (TypeError, ValueError):
                pass
        if isinstance(data.get("detected_emotion"), str):
            self.detected_emotion = data["detected_emotion"].strip() or "neutral"
        if isinstance(data.get("help_mode"), bool):
            self.help_mode = data["help_mode"]

    def _apply_meta_chat(self, data: dict[str, Any]) -> None:
        if "last_real_chat_at" in data:
            try:
                self.last_real_chat_at = float(data["last_real_chat_at"])
            except (TypeError, ValueError):
                pass
        if isinstance(data.get("last_real_chat_login"), str):
            self.last_real_chat_login = data["last_real_chat_login"].strip().lower()
        if isinstance(data.get("last_real_chat_display"), str):
            self.last_real_chat_display = data["last_real_chat_display"].strip()

    def _apply_meta_control(self, data: dict[str, Any]) -> None:
        if "quiet_until" in data:
            try:
                self.quiet_until = float(data["quiet_until"])
            except (TypeError, ValueError):
                pass
        if isinstance(data.get("stopped"), bool):
            self.stopped = data["stopped"]
        if isinstance(data.get("manual_topic"), str):
            self.manual_topic = data["manual_topic"].strip()
        next_speaker = data.get("next_speaker")
        self.next_speaker_hint = next_speaker if isinstance(next_speaker, dict) else None

    def refresh_meta(self) -> None:
        if self.meta_path is None:
            return
        with _file_lock(self.meta_path):
            data = _read_json(self.meta_path, {})
        if not isinstance(data, dict):
            return
        self._apply_meta_game(data)
        self._apply_meta_signal(data)
        self._apply_meta_chat(data)
        self._apply_meta_control(data)

    def _read_recent_payload_unlocked(self) -> dict[str, Any]:
        data = _read_json(self.recent_messages_path, {"messages": []})
        if isinstance(data, dict) and isinstance(data.get("messages"), list):
            return data
        return {"messages": []}

    def _write_recent_payload_unlocked(self, data: dict[str, Any]) -> None:
        _atomic_write_json(self.recent_messages_path, data)

    def prune_recent_messages(self, ttl_s: float = 120.0) -> None:
        now = time.time()
        with _file_lock(self.recent_messages_path):
            data = self._read_recent_payload_unlocked()
            data["messages"] = _prune_messages(data.get("messages", []), now, ttl_s)
            self._write_recent_payload_unlocked(data)

    def is_global_duplicate(self, message: str, ttl_s: float = 120.0) -> bool:
        norm = _norm_msg(message)
        if not norm:
            return False
        now = time.time()
        with _file_lock(self.recent_messages_path):
            data = self._read_recent_payload_unlocked()
            kept = _prune_messages(data.get("messages", []), now, ttl_s)
            dup = any(row.get("norm") == norm for row in kept)
            data["messages"] = kept
            self._write_recent_payload_unlocked(data)
        return dup

    def _recent_limit_blocked(
        self,
        kept: list[dict[str, Any]],
        now: float,
        max_per_minute: int | None,
        min_interval_s: float,
        max_per_window: int | None,
        window_s: float,
    ) -> bool:
        if max_per_minute is not None and max_per_minute >= 0:
            if sum(1 for row in kept if now - float(row.get("ts", 0)) <= 60.0) >= max_per_minute:
                return True
        if min_interval_s > 0 and kept:
            latest = max(float(row.get("ts", 0)) for row in kept)
            if now - latest < min_interval_s:
                return True
        if max_per_window is None or max_per_window < 0:
            return False
        window_count = sum(1 for row in kept if now - float(row.get("ts", 0)) <= window_s)
        return window_count >= max_per_window

    def try_remember_global_message(
        self,
        bot_name: str,
        message: str,
        ttl_s: float = 120.0,
        max_per_minute: int | None = None,
        min_interval_s: float = 0.0,
        max_per_window: int | None = None,
        window_s: float = 300.0,
    ) -> bool:
        norm = _norm_msg(message)
        if not norm:
            return False
        now = time.time()
        with _file_lock(self.recent_messages_path):
            data = self._read_recent_payload_unlocked()
            kept = _prune_messages(data.get("messages", []), now, ttl_s)
            if any(row.get("norm") == norm for row in kept):
                data["messages"] = kept
                self._write_recent_payload_unlocked(data)
                return False
            if self._recent_limit_blocked(kept, now, max_per_minute, min_interval_s, max_per_window, window_s):
                data["messages"] = kept
                self._write_recent_payload_unlocked(data)
                return False
            kept.append({"bot": bot_name, "norm": norm, "raw": message, "ts": now})
            data["messages"] = kept[-120:]
            self._write_recent_payload_unlocked(data)
        return True

    def remember_global_message(self, bot_name: str, message: str, ttl_s: float = 120.0) -> None:
        self.try_remember_global_message(bot_name, message, ttl_s=ttl_s)
