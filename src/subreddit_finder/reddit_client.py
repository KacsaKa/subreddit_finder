from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx


class RedditAPIError(RuntimeError):
    """Raised for non-retryable Reddit API failures."""


@dataclass(slots=True)
class RedditCredentials:
    client_id: str
    client_secret: str
    username: str
    password: str
    user_agent: str


class RedditClient:
    BASE_URL = "https://oauth.reddit.com"
    TOKEN_URL = "https://www.reddit.com/api/v1/access_token"

    def __init__(self, creds: RedditCredentials, requests_per_second: float = 1.0) -> None:
        self.creds = creds
        self._client = httpx.AsyncClient(timeout=30.0)
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._last_request_at = 0.0
        self._rps = requests_per_second
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    async def _authenticate(self) -> None:
        response = await self._client.post(
            self.TOKEN_URL,
            data={
                "grant_type": "password",
                "username": self.creds.username,
                "password": self.creds.password,
            },
            auth=(self.creds.client_id, self.creds.client_secret),
            headers={"User-Agent": self.creds.user_agent},
        )
        response.raise_for_status()
        payload = response.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + int(payload.get("expires_in", 3600)) - 60

    async def _ensure_token(self) -> None:
        if not self._token or time.time() >= self._token_expires_at:
            await self._authenticate()

    async def _throttle(self) -> None:
        min_delay = 1.0 / self._rps
        elapsed = time.time() - self._last_request_at
        if elapsed < min_delay:
            await asyncio.sleep(min_delay - elapsed)

    async def request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with self._lock:
            await self._ensure_token()
            await self._throttle()

            headers = {
                "Authorization": f"bearer {self._token}",
                "User-Agent": self.creds.user_agent,
            }

            attempts = 0
            while True:
                attempts += 1
                response = await self._client.get(
                    f"{self.BASE_URL}{path}", params=params or {}, headers=headers
                )
                self._last_request_at = time.time()

                if response.status_code in {429, 500, 502, 503, 504} and attempts < 5:
                    await asyncio.sleep(min(2**attempts, 10))
                    continue

                if response.status_code >= 400:
                    raise RedditAPIError(
                        f"Reddit request failed for {path}: {response.status_code} {response.text[:300]}"
                    )
                return response.json()
