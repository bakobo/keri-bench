"""The fault proxy that owns every link between roles (this.i 0wcr2rrj; test plan, Faults).

Every link in the stacks under test is HTTP/1.1, so the proxy parses HTTP rather than relaying
bytes: a ``duplicate`` fault must replay one whole message, which a byte-level TCP proxy cannot
delimit. It parses only to find message boundaries and never rewrites a byte, Host included,
because clients sign ``@authority`` under RFC 9421 and the upstream verifies against what it
receives.

Messages are buffered whole, bounded by ``max_header`` and ``max_body``, before they are passed
on. That costs nothing for the small messages KERI roles exchange and keeps every fault a
statement about whole messages.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import BenchError

E_HEADER_SIZE = "e.input.range.proxy.header.f"
E_BODY_SIZE = "e.input.range.proxy.body.f"
E_REQUEST_FORMAT = "e.input.format.proxy.request.f"
E_UPSTREAM = "e.env.proxy.upstream.r"
E_RESPONSE = "e.env.proxy.response.f"
E_DELAY = "e.input.range.proxy.delay.f"
E_UPSTREAM_URL = "e.input.format.proxy.upstream.f"
E_NOT_STARTED = "e.state.missing.proxy.listener.f"
E_SAMPLES = "e.input.range.proxy.samples.f"

_TOKEN = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_REQUEST_LINE = re.compile(rb"([!#$%&'*+.^_`|~0-9A-Za-z-]+) (\S+) HTTP/1\.[01]")
_STATUS_LINE = re.compile(rb"HTTP/1\.[01] ([0-9]{3})(?: [^\r\n]*)?")
_HEX = re.compile(rb"[0-9A-Fa-f]+")
_REASONS = {400: "Bad Request", 413: "Content Too Large", 431: "Request Header Fields Too Large",
            502: "Bad Gateway"}


@dataclass(frozen=True)
class Exchange:
    """One request seen by a link, so an adapter can turn a boundary into an ``observed`` event.

    Times are the link clock's nanoseconds. ``t_request`` is when the whole request had been
    read (or when it was refused), ``t_forwarded`` when it was written upstream, and
    ``t_response`` when the whole response had been relayed to the client. ``status`` is None
    when nothing answered; ``error`` carries the code of a refusal or an upstream failure.
    """

    link: str
    method: str
    path: str
    status: int | None
    t_request: int
    t_forwarded: int | None
    t_response: int | None
    duplicate: bool = False
    dropped: bool = False
    error: str | None = None


class _Bad(Exception):
    """A message that cannot be delimited safely; ``kind`` picks the status on the request side."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


_REQUEST_REFUSALS = {"format": (400, E_REQUEST_FORMAT), "header": (431, E_HEADER_SIZE),
                     "body": (413, E_BODY_SIZE)}


async def _until(reader: asyncio.StreamReader, separator: bytes) -> bytes:
    """Read through ``separator``; the stream's limit is what bounds a header section or line."""
    try:
        return await reader.readuntil(separator)
    except asyncio.LimitOverrunError:
        raise _Bad("header", "A header section or chunk line is longer than the limit.") from None


def _fields(head: bytes) -> tuple[bytes, dict[str, list[str]]]:
    """Split a head into its start line and lower-cased fields, refusing anything ambiguous."""
    lines = head[:-4].split(b"\r\n")
    fields: dict[str, list[str]] = {}
    for line in lines[1:]:
        name, colon, value = line.partition(b":")
        if not colon or not _TOKEN.fullmatch(name):
            raise _Bad("format", "A header line is not a well-formed field.")
        fields.setdefault(name.decode().lower(), []).append(value.strip().decode("latin-1"))
    return lines[0], fields


def _framing(fields: dict[str, list[str]], max_body: int) -> int | str | None:
    """Return the body length, ``"chunked"``, or None when the message declares no framing."""
    codings = [c.strip().lower() for v in fields.get("transfer-encoding", []) for c in v.split(",")]
    lengths = {c.strip() for v in fields.get("content-length", []) for c in v.split(",")}
    if codings and lengths:
        raise _Bad("format", "The message has both Transfer-Encoding and Content-Length.")
    if codings:
        if codings[-1] != "chunked":
            raise _Bad("format", "The only transfer coding the proxy can delimit is chunked.")
        return "chunked"
    if not lengths:
        return None
    if len(lengths) != 1 or not (length := lengths.pop()).isdigit():
        raise _Bad("format", "The Content-Length is not one decimal number.")
    if int(length) > max_body:
        raise _Bad("body", "The body is longer than the limit.")
    return int(length)


async def _chunked(reader: asyncio.StreamReader, max_body: int, max_header: int) -> bytes:
    """Read a chunked body exactly as sent, counting data against max_body and trailers against
    max_header."""
    parts, total = [], 0
    while True:
        line = await _until(reader, b"\r\n")
        parts.append(line)
        size = line[:-2].split(b";", 1)[0].strip()
        if not _HEX.fullmatch(size):
            raise _Bad("format", "A chunk size is not a hexadecimal number.")
        if (n := int(size, 16)) == 0:
            break
        total += n
        if total > max_body:
            raise _Bad("body", "The body is longer than the limit.")
        data = await reader.readexactly(n + 2)
        if data[-2:] != b"\r\n":
            raise _Bad("format", "A chunk is not followed by CRLF.")
        parts.append(data)
    trailers = 0
    while (line := await _until(reader, b"\r\n")) != b"\r\n":
        parts.append(line)
        trailers += len(line)
        if trailers > max_header:
            raise _Bad("header", "The trailer section is longer than the limit.")
    parts.append(line)
    return b"".join(parts)


async def _to_eof(reader: asyncio.StreamReader, max_body: int) -> bytes:
    """Read a close-delimited body."""
    parts, total = [], 0
    while data := await reader.read(65536):
        total += len(data)
        if total > max_body:
            raise _Bad("body", "The body is longer than the limit.")
        parts.append(data)
    return b"".join(parts)


async def _body(reader, framing, max_body, max_header, *, close_delimited: bool) -> bytes:
    if framing == "chunked":
        return await _chunked(reader, max_body, max_header)
    if isinstance(framing, int):
        return await reader.readexactly(framing)
    return await _to_eof(reader, max_body) if close_delimited else b""


def _closes(fields: dict[str, list[str]]) -> bool:
    return any(t.strip().lower() == "close" for v in fields.get("connection", []) for t in v.split(","))


async def _read_response(reader, method: str, max_body: int, max_header: int):
    """Read one final response, with any interim 1xx responses before it, as raw bytes.

    Returns (status, raw, closes), where ``closes`` says the response was delimited by the
    upstream closing the connection. A ``Connection: close`` header alone does not count: what
    ends a connection is the upstream closing it, which the proxy watches for between requests
    (keripy's witnesses send the header and keep the socket open, and keripy's client goes on
    using it).
    """
    parts = []
    while True:
        head = await _until(reader, b"\r\n\r\n")
        start, fields = _fields(head)
        if not (m := _STATUS_LINE.fullmatch(start)):
            raise _Bad("format", "The status line is not HTTP/1.x.")
        status = int(m[1])
        parts.append(head)
        if status >= 200:
            break
    if method == "HEAD" or status in (204, 304):
        return status, b"".join(parts), False
    framing = _framing(fields, max_body)
    parts.append(await _body(reader, framing, max_body, max_header, close_delimited=True))
    return status, b"".join(parts), framing is None


def _refusal(status: int, code: str, message: str) -> bytes:
    body = f"{code}: {message}\n".encode()
    return (f"HTTP/1.1 {status} {_REASONS[status]}\r\nContent-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body


class Link:
    """One reverse proxy in front of one upstream endpoint, with faults switchable at runtime."""

    def __init__(self, name: str, upstream: str, *, clock: Callable[[], int] = time.monotonic_ns,
                 on_exchange: Callable[[Exchange], object] | None = None, max_header: int = 65536,
                 max_body: int = 64 * 2**20) -> None:
        parts = urlsplit(upstream)
        try:
            port = parts.port
        except ValueError:
            port = None
        if parts.scheme != "http" or not parts.hostname or port is None:
            raise BenchError(E_UPSTREAM_URL, f"The upstream {upstream!r} is not an http://host:port "
                                             "URL, so the link has nowhere to forward to.")
        self.name, self._host, self._port = name, parts.hostname, port
        self._clock, self._on_exchange = clock, on_exchange
        self.max_header, self.max_body = max_header, max_body
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task] = set()
        self._partition: asyncio.Event | None = None
        self._delay: int | Callable[[], int] = 0
        self._duplicate = False

    # --- lifecycle ---

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0, limit=self.max_header)

    async def stop(self) -> None:
        """Close the listener and every connection, including ones a partition is holding."""
        self._server.close()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._server.wait_closed()

    async def __aenter__(self) -> Link:
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    @property
    def url(self) -> str:
        if self._server is None:
            raise BenchError(E_NOT_STARTED, f"The link {self.name!r} has not been started, so it has "
                                            "no address yet.")
        return f"http://127.0.0.1:{self._server.sockets[0].getsockname()[1]}"

    # --- faults ---

    def delay(self, fn_or_ns: int | Callable[[], int]) -> None:
        """Add latency before each request is forwarded: fixed nanoseconds, or a zero-argument
        callable drawn once per request so a distribution can be plugged in. A negative draw
        means no delay, so a distribution need not be truncated by its caller."""
        if not callable(fn_or_ns) and (type(fn_or_ns) is not int or fn_or_ns < 0):
            raise BenchError(E_DELAY, f"A delay must be a non-negative integer of nanoseconds or a "
                                      f"callable returning one, and {fn_or_ns!r} is neither.")
        self._delay = fn_or_ns

    def partition(self) -> None:
        """Drop traffic: requests are read but not forwarded, and their clients get no answer."""
        if self._partition is None:
            self._partition = asyncio.Event()

    def heal(self) -> None:
        """End a partition: close the connections it held, and forward new requests again."""
        if self._partition is not None:
            self._partition.set()
            self._partition = None

    def duplicate(self, on: bool) -> None:
        """Forward each request twice, the copy over a fresh upstream connection."""
        self._duplicate = on

    def clear(self) -> None:
        self._delay, self._duplicate = 0, False
        self.heal()

    # --- serving ---

    def _emit(self, exchange: Exchange) -> None:
        if self._on_exchange is not None:
            self._on_exchange(exchange)

    def _track(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._track(asyncio.current_task())
        upstream: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None
        try:
            while True:
                method = path = ""
                try:
                    head = await self._next_request(reader, upstream)
                    if head is None:
                        return  # the upstream ended the connection, so the proxy does too
                    start, fields = _fields(head)
                    if not (m := _REQUEST_LINE.fullmatch(start)):
                        raise _Bad("format", "The request line is not METHOD target HTTP/1.x.")
                    method, path = m[1].decode(), m[2].decode("latin-1")
                    framing = _framing(fields, self.max_body)
                    raw = head + await _body(reader, framing, self.max_body, self.max_header,
                                             close_delimited=False)
                except asyncio.IncompleteReadError:
                    return
                except _Bad as bad:
                    status, code = _REQUEST_REFUSALS[bad.kind]
                    await self._refuse(writer, method, path, self._clock(), None, status, code,
                                       bad.message)
                    return
                t_request = self._clock()
                if (held := self._partition) is not None:
                    self._emit(Exchange(self.name, method, path, None, t_request, None, None,
                                        dropped=True))
                    await held.wait()
                    return
                ns = self._delay() if callable(self._delay) else self._delay
                if ns > 0:
                    await asyncio.sleep(ns / 1e9)
                t_forwarded = None
                if self._duplicate:  # independent of the original, which may fail on its own
                    self._track(asyncio.create_task(self._replay(raw, method, path, t_request)))
                try:
                    if upstream is None:
                        upstream = await asyncio.open_connection(self._host, self._port,
                                                                 limit=self.max_header)
                    upstream[1].write(raw)
                    t_forwarded = self._clock()
                    await upstream[1].drain()
                    status, response, closes = await _read_response(
                        upstream[0], method, self.max_body, self.max_header)
                except (OSError, asyncio.IncompleteReadError):
                    await self._refuse(writer, method, path, t_request, t_forwarded, 502, E_UPSTREAM,
                                       "The upstream could not be reached or closed the connection "
                                       "without a whole response.")
                    return
                except _Bad as bad:
                    await self._refuse(writer, method, path, t_request, t_forwarded, 502, E_RESPONSE,
                                       f"The upstream's response could not be relayed. {bad.message}")
                    return
                t_response = None
                try:
                    writer.write(response)
                    await writer.drain()
                    t_response = self._clock()
                finally:
                    self._emit(Exchange(self.name, method, path, status, t_request, t_forwarded,
                                        t_response))
                if closes or _closes(fields):
                    return
        except OSError:
            pass  # the client went away; there is no one left to answer
        finally:
            writer.close()
            if upstream is not None:
                upstream[1].close()

    @staticmethod
    async def _next_request(reader, upstream) -> bytes | None:
        """The client's next request head, or None if the upstream closes (or speaks unasked)
        while the proxy waits for it, so the client sees the connection end as it would have."""
        if upstream is None:
            return await _until(reader, b"\r\n\r\n")
        head = asyncio.ensure_future(_until(reader, b"\r\n\r\n"))
        ended = asyncio.ensure_future(upstream[0].read(1))
        done, _ = await asyncio.wait({head, ended}, return_when=asyncio.FIRST_COMPLETED)
        if ended not in done:
            ended.cancel()  # and wait, so the stream has no reader left when the response is read
            with contextlib.suppress(asyncio.CancelledError):
                await ended
            if not ended.cancelled():
                head.cancel()  # pragma: no cover - the upstream spoke in the same instant
                return None  # pragma: no cover
            return head.result()
        head.cancel()
        # The client may have hung up in the same instant; either way nothing is left to read.
        with contextlib.suppress(asyncio.CancelledError, asyncio.IncompleteReadError, _Bad,
                                 OSError):
            await head
        return None

    async def _refuse(self, writer, method, path, t_request, t_forwarded, status, code, message):
        writer.write(_refusal(status, code, message))
        await writer.drain()
        self._emit(Exchange(self.name, method, path, status, t_request, t_forwarded, self._clock(),
                            error=code))

    async def _replay(self, raw: bytes, method: str, path: str, t_request: int) -> None:
        """Send the duplicate copy on its own connection and discard its response."""
        status = t_forwarded = t_response = error = None
        try:
            reader, writer = await asyncio.open_connection(self._host, self._port,
                                                           limit=self.max_header)
            try:
                writer.write(raw)
                t_forwarded = self._clock()
                await writer.drain()
                status, _, _ = await _read_response(reader, method, self.max_body, self.max_header)
                t_response = self._clock()
            finally:
                writer.close()
        except (OSError, asyncio.IncompleteReadError):
            error = E_UPSTREAM
        except _Bad:
            error = E_RESPONSE
        self._emit(Exchange(self.name, method, path, status, t_request, t_forwarded, t_response,
                            duplicate=True, error=error))


# --- measurement cost ---

_PING = b"GET /ping HTTP/1.1\r\nHost: peer\r\n\r\n"
_PONG = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"


async def _pong(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(_PONG)
    except (asyncio.IncompleteReadError, OSError):
        writer.close()


async def _round_trip(conn) -> int:
    reader, writer = conn
    t0 = time.perf_counter_ns()
    writer.write(_PING)
    await reader.readexactly(len(_PONG))
    return time.perf_counter_ns() - t0


def _nearest_rank(ordered: list[int], p: int) -> int:
    return ordered[max(0, -(-p * len(ordered) // 100) - 1)]


async def measure_pass_through(samples: int = 200) -> dict:
    """Measure what a link adds to a round trip, against a trivial peer on this host.

    Direct and proxied round trips alternate so that drift in the host's load lands on both, and
    each sample is the difference of one adjacent pair. This is the measurement cost a run
    reports per link (test plan, Statistics).
    """
    if samples < 1:
        raise BenchError(E_SAMPLES, f"Measuring pass-through needs at least one sample, and "
                                    f"{samples} were asked for.")
    peer = await asyncio.start_server(_pong, "127.0.0.1", 0)
    port = peer.sockets[0].getsockname()[1]
    added = []
    try:
        async with Link("pass-through", f"http://127.0.0.1:{port}") as link:
            direct = await asyncio.open_connection("127.0.0.1", port)
            via = await asyncio.open_connection("127.0.0.1", urlsplit(link.url).port)
            for i in range(samples + 20):  # the first 20 pairs warm both paths and are discarded
                d, v = await _round_trip(direct), await _round_trip(via)
                if i >= 20:
                    added.append(v - d)
            for _, writer in (direct, via):
                writer.close()
    finally:
        peer.close()
        peer.close_clients()
        await peer.wait_closed()
    added.sort()
    return {"samples": samples, "p50_ns": _nearest_rank(added, 50),
            "p99_ns": _nearest_rank(added, 99)}
