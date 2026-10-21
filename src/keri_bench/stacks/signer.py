"""The signer hsm-sweep puts behind imbu's principal (this.i 2p26rn9h).

heti's ``ThrottledSigner`` over a ``SoftwareSigner`` makes signing behave like an HSM with a given
latency per operation and a cap on operation starts per second (heti @5dmsz9h5). This module adds
only a record: ``RecordingProfile`` is heti's ``HsmProfile`` schedule, its ``wait_turn`` copied
from heti f568f7e2 (src/heti/anchored/produce/signers.py) so the moment a start is claimed can be
read, and it notes for every operation when it was asked for, when it started and when its
latency ended, on the host's monotonic clock in nanoseconds, which is the harness's clock domain
(this.i 98rxw488). ``CountingSigner`` names each operation's kind. The issuer process serves the
record at ``GET /signer`` on a loopback port, and the adapter reads it once the drain ends.
"""

from __future__ import annotations

import asyncio
import time

from heti.anchored.produce import HsmProfile, ThrottledSigner


class RecordingProfile(HsmProfile):
    def __init__(self, *, ns=time.monotonic_ns, **device) -> None:
        super().__init__(**device)
        self._ns = ns
        self.kind = "sign"
        self.operations: list[list] = []

    async def wait_turn(self) -> None:
        kind, self.kind = self.kind, "sign"
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
             sleep=asyncio.sleep) -> CountingSigner:
    profile = RecordingProfile(latency=(latency_ms or 0.0) / 1e3, rate=rate, clock=clock,
                               sleep=sleep)
    return CountingSigner(inner, profile=profile, latency_ms=latency_ms, rate=rate)


def handler(counted: CountingSigner):
    async def handle(method: str, path: str, body: bytes):
        if method == "GET" and path == "/signer":
            return 200, counted.report()
        return 404, {"error": f"There is no route {method} {path}."}
    return handle
