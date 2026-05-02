import re
import time
from pathlib import Path
from typing import Any, Iterable

from config import DIVBOTS_VIEWERS_FILE
from shared import _atomic_write_json, _file_lock, _read_json

LOGIN_RE = re.compile(r"^[a-z0-9_]{2,25}$")
DISPLAY_RE = re.compile(r"^[A-Za-z0-9_]{2,25}$")


def clean_login(raw: str) -> str:
    value = (raw or "").strip().lower()
    return value if LOGIN_RE.fullmatch(value) else ""


def clean_display(raw: str, fallback: str) -> str:
    value = (raw or "").strip()
    if DISPLAY_RE.fullmatch(value):
        return value
    return fallback


def _int_value(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _float_value(raw: Any, default: float = 0.0) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


class DivBotViewers:
    def __init__(self, path: str | Path = DIVBOTS_VIEWERS_FILE):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _payload(self) -> dict[str, Any]:
        data = _read_json(self.path, {"viewers": {}})
        if not isinstance(data, dict):
            return {"viewers": {}}
        if not isinstance(data.get("viewers"), dict):
            data["viewers"] = {}
        return data

    def observe_message(
        self,
        login: str,
        display_name: str = "",
        *,
        is_mod: bool = False,
        is_broadcaster: bool = False,
        now: float | None = None,
    ) -> bool:
        login = clean_login(login)
        if not login:
            return False
        now = time.time() if now is None else now
        display = clean_display(display_name, login)
        with _file_lock(self.path):
            data = self._payload()
            viewers = data["viewers"]
            row = viewers.get(login) if isinstance(viewers.get(login), dict) else {}
            row["login"] = login
            row["display"] = display
            row["count"] = _int_value(row.get("count"), 0) + 1
            row.setdefault("first_seen_at", now)
            row["last_seen_at"] = now
            row["is_mod"] = bool(row.get("is_mod", False) or is_mod)
            row["is_broadcaster"] = bool(row.get("is_broadcaster", False) or is_broadcaster)
            viewers[login] = row
            data["ts"] = now
            _atomic_write_json(self.path, data)
        return True

    def helper_viewer_callout(
        self,
        login: str = "",
        *,
        exclude_logins: Iterable[str] = (),
        top_n: int = 20,
        cooldown_s: float = 1800.0,
        active_within_s: float = 900.0,
        now: float | None = None,
    ) -> str | None:
        now = time.time() if now is None else now
        wanted = clean_login(login)
        exclude = {clean_login(x) for x in exclude_logins}
        exclude.discard("")
        with _file_lock(self.path):
            data = self._payload()
            viewers = data["viewers"]
            rows = [row for row in viewers.values() if isinstance(row, dict)]
            rows = [
                row
                for row in rows
                if clean_login(str(row.get("login") or ""))
                and clean_login(str(row.get("login") or "")) not in exclude
                and not bool(row.get("is_broadcaster"))
                and _int_value(row.get("count"), 0) >= 2
            ]
            rows.sort(key=lambda row: (-_int_value(row.get("count"), 0), -_float_value(row.get("last_seen_at"), 0.0)))
            top = rows[: max(1, top_n)]
            if wanted:
                top = [row for row in top if clean_login(str(row.get("login") or "")) == wanted]
            candidates = []
            for row in top:
                try:
                    last_seen = float(row.get("last_seen_at", 0))
                    last_callout = float(row.get("last_callout_at", 0))
                except (TypeError, ValueError):
                    continue
                if now - last_seen > active_within_s:
                    continue
                if last_callout > 0 and now - last_callout < cooldown_s:
                    continue
                candidates.append(row)
            if not candidates:
                return None
            pick = candidates[0]
            pick["last_callout_at"] = now
            data["ts"] = now
            _atomic_write_json(self.path, data)
            display = clean_display(str(pick.get("display") or ""), str(pick.get("login") or "chat"))
            return f"yo {display}"

    def snapshot(self) -> dict[str, Any]:
        with _file_lock(self.path):
            data = self._payload()
        viewers = data.get("viewers", {})
        return {
            "count": len(viewers) if isinstance(viewers, dict) else 0,
            "ts": data.get("ts"),
        }
