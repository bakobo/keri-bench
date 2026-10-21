"""The signer hsm-sweep puts behind imbu's principal (this.i 2p26rn9h).

heti's ``ThrottledSigner`` over a ``SoftwareSigner`` makes signing behave like an HSM with a given
latency per operation and a cap on operation starts per second (heti @5dmsz9h5). This module adds
only a record: ``RecordingProfile`` is heti's ``HsmProfile`` schedule, its ``wait_turn`` copied
from heti f568f7e2 (src/heti/anchored/produce/signers.py) so the moment a start is claimed can be
read, and it notes for every operation when it was asked for, when it started and when its
latency ended, on the host's monotonic clock in nanoseconds, which is the harness's clock domain
(this.i 98rxw488). ``CountingSigner`` names each operation's kind. The issuer process serves the
record at ``GET /signer`` on a loopback port, and the adapter reads it once the drain ends.

Given a ``Device``, the profile keeps its whole schedule, start claims and latency, on that
device's own event loop in a daemon thread, which does nothing else and so wakes on time; the
caller awaits the answer. imbu's own loop then decides only when imbu notices the answer, which
is imbu's time, not the device's (this.i 0v8kmser).
"""

from __future__ import annotations

import asyncio
import threading
import time

from heti.anchored.produce import HsmProfile, ThrottledSigner


class Device:
    """An event loop in a daemon thread that does nothing but keep an HSM's schedule."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="hsm-device",
                                       daemon=True)
        self.thread.start()

    async def run(self, coroutine):
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coroutine, self.loop))

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


class RecordingProfile(HsmProfile):
    def __init__(self, *, ns=time.monotonic_ns, device: Device | None = None, **hsm) -> None:
        super().__init__(**hsm)
        self._ns = ns
        self._device = device
        self.kind = "sign"
        self.operations: list[list] = []
        self.threads: set[str] = set()

    async def wait_turn(self) -> None:
        kind, self.kind = self.kind, "sign"
        if self._device is None:
            return await self._turn(kind)
        return await self._device.run(self._turn(kind))

    async def _turn(self, kind: str) -> None:
        self.threads.add(threading.current_thread().name)
        requested = self._ns()
        while (now := self._clock()) < self._free:
            await self._sleep(self._free - now)
        self._free = now + self._spacing
        started = self._ns()
        if self._latency:
            await self._sleep(self._latency)
        self.operations.append([kind, requested, started, self._ns()])


class CountingSigner(ThrottledSigner):
    """heti's ThrottledSigner, telling its profile which operation each turn is for. Setting the
    kind and entering ``wait_turn`` happen with no await between them, so on one event loop no
    other operation can take the turn in between."""

    def __init__(self, inner, *, profile: RecordingProfile, latency_ms, rate) -> None:
        super().__init__(inner, profile=profile)
        self.profile, self.latency_ms, self.rate = profile, latency_ms, rate

    async def incept(self):
        self.profile.kind = "incept"
        return await super().incept()

    async def rotate(self, *, current=None, last: bool = False):
        self.profile.kind = "rotate"
        return await super().rotate(current=current, last=last)

    def report(self) -> dict:
        return {"latency_ms": self.latency_ms, "rate": self.rate,
                "operations": list(self.profile.operations)}


def counting(inner, *, latency_ms: float | None, rate: float | None, clock=time.monotonic,
             sleep=asyncio.sleep, device: Device | None = None) -> CountingSigner:
    profile = RecordingProfile(latency=(latency_ms or 0.0) / 1e3, rate=rate, clock=clock,
                               sleep=sleep, device=device)
    return CountingSigner(inner, profile=profile, latency_ms=latency_ms, rate=rate)


def handler(counted: CountingSigner):
    async def handle(method: str, path: str, body: bytes):
        if method == "GET" and path == "/signer":
            return 200, counted.report()
        return 404, {"error": f"There is no route {method} {path}."}
    return handle
