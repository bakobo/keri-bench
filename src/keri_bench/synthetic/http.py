"""A minimal HTTP/1.1 JSON server for the synthetic roles.

Only what the calibration stack needs: Content-Length bodies, keep-alive, JSON answers. Bounded
like every other door (input-handling standard): the head and the body have fixed limits.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

MAX_HEAD = 16 * 1024
MAX_BODY = 1 << 20

Handler = Callable[[str, str, bytes], Awaitable[tuple[int, object]]]

_REASONS = {200: "OK", 202: "Accepted", 400: "Bad Request", 404: "Not Found",
            413: "Content Too Large", 500: "Internal Server Error"}


def _answer(status: int, payload: object) -> bytes:
    body = json.dumps(payload).encode()
    head = (f"HTTP/1.1 {status} {_REASONS.get(status, 'Status')}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n")
    return head.encode() + body


async def _connection(handler: Handler, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            try:
                head = await reader.readuntil(b"\r\n\r\n")
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
                return
            lines = head.decode("latin-1").split("\r\n")
            parts = lines[0].split(" ")
            length = 0
            for line in lines[1:]:
                name, _, value = line.partition(":")
                if name.strip().lower() == "content-length":
                    length = int(value.strip()) if value.strip().isdigit() else -1
            if len(parts) != 3 or length < 0 or length > MAX_BODY:
                status = 413 if length > MAX_BODY else 400
                writer.write(_answer(status, {"error": "The request was malformed or too large."}))
                await writer.drain()
                return
            body = await reader.readexactly(length) if length else b""
            try:
                status, payload = await handler(parts[0], parts[1], body)
            except Exception as wrong:  # a synthetic role's bug must answer, not hang a client
                status, payload = 500, {"error": f"{type(wrong).__name__}: {wrong}"}
            writer.write(_answer(status, payload))
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        return
    finally:
        writer.close()


async def serve(handler: Handler, port: int, host: str = "127.0.0.1") -> asyncio.Server:
    return await asyncio.start_server(lambda r, w: _connection(handler, r, w), host, port,
                                      limit=MAX_HEAD)
