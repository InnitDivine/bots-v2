import asyncio
import json
import time
from pathlib import Path
from typing import Any, Optional

import aiohttp

from config import (
    HELIX_CACHE_FILE,
    HELIX_CACHE_TTL_S,
    TARGET_CHANNEL,
    TWITCH_CLIENT_ID,
    TWITCH_CLIENT_SECRET,
)


class Helix:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.client_id = TWITCH_CLIENT_ID
        self.client_secret = TWITCH_CLIENT_SECRET
        self._token = ""
        self._token_exp = 0.0
        self._last_stream_fetch = 0.0
        self.title = ""
        self.game = ""
        self._cache_path = Path(HELIX_CACHE_FILE)
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache = self._load_cache()

    def _load_cache(self) -> dict[str, Any]:
        if not self._cache_path.exists():
            return {}
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def _save_cache(self):
        tmp = self._cache_path.with_suffix(self._cache_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._cache_path)

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        params: Optional[dict[str, str]] = None,
        timeout: float = 10.0,
        retries: int = 3,
    ) -> dict[str, Any]:
        last_err: Exception | None = None
        for i in range(retries):
            try:
                async with self.session.request(method, url, headers=headers, params=params, timeout=timeout) as r:
                    if r.status < 400:
                        return await r.json()

                    body = await r.text()
                    retry_after = r.headers.get("Retry-After")
                    if r.status in (429, 500, 502, 503, 504) and i < retries - 1:
                        delay = float(retry_after) if retry_after else min(20.0, 1.5 * (2**i))
                        await asyncio.sleep(delay)
                        continue
                    raise RuntimeError(f"Helix HTTP {r.status}: {body[:180]}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last_err = e
                if i < retries - 1:
                    await asyncio.sleep(min(20.0, 1.2 * (2**i)))
                    continue
                raise RuntimeError(f"Helix request failed: {last_err}")
        raise RuntimeError(f"Helix request failed: {last_err}")

    async def _ensure_app_token(self):
        now = time.time()
        if self._token and now < self._token_exp - 60:
            return

        data = await self._request_json(
            "POST",
            "https://id.twitch.tv/oauth2/token",
            params={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            },
            retries=3,
        )
        self._token = data["access_token"]
        self._token_exp = now + float(data.get("expires_in", 3600))

    def _headers(self) -> dict[str, str]:
        return {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self._token}",
        }

    def _cache_fresh(self, key: str, ttl_s: int) -> bool:
        row = self._cache.get(key)
        if not isinstance(row, dict):
            return False
        ts = float(row.get("ts", 0))
        return (time.time() - ts) <= ttl_s

    async def get_stream_info(self) -> tuple[str, str]:
        now = time.time()

        if now - self._last_stream_fetch < 60.0:
            return self.title, self.game

        if self._cache_fresh("stream", HELIX_CACHE_TTL_S):
            row = self._cache.get("stream", {})
            self.title = str(row.get("title", ""))
            self.game = str(row.get("game", ""))

        try:
            await self._ensure_app_token()
            data = await self._request_json(
                "GET",
                "https://api.twitch.tv/helix/streams",
                headers=self._headers(),
                params={"user_login": TARGET_CHANNEL},
            )
            if data.get("data"):
                s = data["data"][0]
                self.title = s.get("title", "")
                self.game = s.get("game_name", "")
            else:
                self.title = ""
                self.game = ""
            self._last_stream_fetch = now
            self._cache["stream"] = {"title": self.title, "game": self.game, "ts": time.time()}
            self._save_cache()
        except Exception as e:
            print(f"helix stream info failed: {e}")
        return self.title, self.game

    async def get_global_emote_names(self) -> list[str]:
        if self._cache_fresh("global_emotes", HELIX_CACHE_TTL_S):
            row = self._cache.get("global_emotes", {})
            cached = row.get("names")
            if isinstance(cached, list) and cached:
                return [str(x) for x in cached]

        try:
            await self._ensure_app_token()
            data = await self._request_json(
                "GET",
                "https://api.twitch.tv/helix/chat/emotes/global",
                headers=self._headers(),
            )
            names = sorted({e.get("name") for e in data.get("data", []) if e.get("name")})
            self._cache["global_emotes"] = {"names": names, "ts": time.time()}
            self._save_cache()
            return names
        except Exception as e:
            print(f"helix global emotes failed: {e}")
            row = self._cache.get("global_emotes", {})
            cached = row.get("names")
            if isinstance(cached, list):
                return [str(x) for x in cached]
            return []
