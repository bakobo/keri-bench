"""The HTTP client every adapter uses, so the calibration stack measures the same client code a
real adapter runs (this.i 2vu4969q).

aiohttp, because the client runs on the harness's event loop beside the driver: httpx's pool
spent most of a calibration run's CPU scanning its connections, and that showed up as send lag.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import aiohttp

MAX_ANSWER = 64 * 2**20
"""Bytes. A larger answer is refused rather than buffered (input-handling standard)."""


@dataclass(frozen=True)
class Answer:
    status: int
    body: bytes
    headers: Mapping[str, str]

    def json(self) -> Any:
        return json.loads(self.body)


class HttpClient:
    def __init__(self, *, timeout_s: float = 60.0) -> None:
        self.timeout_s = timeout_s
        self._session: aiohttp.ClientSession | None = None

    def _ensure(self) -> aiohttp.ClientSession:
        # Created on first use, inside the running loop that will own it.
        if self._session is None:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=0, force_close=False),
                timeout=aiohttp.ClientTimeout(total=self.timeout_s),
                auto_decompress=False, read_bufsize=1 << 16)
        return self._session

    async def request(self, method: str, url: str, *, body: bytes | None = None,
                      headers: Mapping[str, str] | None = None) -> Answer:
        async with self._ensure().request(method, url, data=body, headers=headers,
                                          skip_auto_headers=("User-Agent",)) as answer:
            # read(n) returns what has arrived, up to n, not the whole body: read to the end.
            data = bytearray()
            while chunk := await answer.content.read(MAX_ANSWER + 1 - len(data)):
                data += chunk
                if len(data) > MAX_ANSWER:
                    raise ValueError(f"The answer from {url} is larger than {MAX_ANSWER} bytes.")
            return Answer(answer.status, bytes(data), dict(answer.headers))

    async def get(self, url: str, **kw) -> Answer:
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw) -> Answer:
        return await self.request("POST", url, **kw)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
