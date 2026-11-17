"""One synthetic role in its own process: ``python -m keri_bench.synthetic.server ROLE ...``.

Every delay a role injects is drawn from a seeded log-normal and appended to the truth file
before it is slept, so the calibration can compare what the harness measured with what happened
(this.i 6he49n0k). The issuer serializes construction behind a fixed service rate: that is the
known throughput cap. Reported times are written on a stack clock skewed from wall time by
``clock_skew_ms``, readable at ``GET /clock``, so the harness must measure the offset to use them.

The spec is JSON: ``{"seed": int, "clock_skew_ms": float, "delays": {"a>b": [median_ms, sigma]},
"service_rate": float, "stall_after": int|null, "stall_ms": float}``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import time
import zlib
from pathlib import Path

from .. import http as client_http
from . import http

ROLES = ("issuer", "state_distribution", "holder", "verifier")


class Role:
    def __init__(self, name: str, spec: dict, truth: Path) -> None:
        self.name, self.spec = name, spec
        self.rng = random.Random(spec["seed"] * 1_000_003 + zlib.crc32(name.encode()))
        self.skew = round(spec.get("clock_skew_ms", 0.0) * 1e6)
        self._truth = open(truth, "a", encoding="utf-8")

    def now(self) -> int:
        return time.time_ns() + self.skew

    def draw(self, transition: str) -> float:
        """Seconds to sleep for one transition, from the role's seeded log-normal."""
        median_ms, sigma = self.spec["delays"][transition]
        return median_ms / 1e3 * math.exp(self.rng.gauss(0.0, sigma))

    def truth(self, credential: str, transition: str, injected: float, elapsed: int,
              floor: int) -> None:
        """Record what was injected and what actually elapsed on this role's monotonic clock.

        The strict gate compares against ``elapsed_ns``: a sleep overshoots, and that overshoot
        is the synthetic stack's own behaviour, not the harness's measurement error.
        ``floor_ns`` is what had elapsed when the effect that ends the transition began, the most
        the harness's end stamp can be held to: for a transition ended by a request to another
        role, ``elapsed_ns`` also counts the answer's way back, which the harness may stamp
        before (this.i 0hykugwr).
        """
        start, end = transition.split(">")
        self._truth.write(json.dumps({"credential": credential, "start": start, "end": end,
                                      "injected_ns": round(injected * 1e9),
                                      "elapsed_ns": elapsed, "floor_ns": floor}) + "\n")
        self._truth.flush()

    async def delay(self, credential: str, transition: str, then=None) -> None:
        """Sleep a drawn delay, run ``then`` (the effect that ends the transition, if it is not
        the sleep itself), and record the whole as truth."""
        seconds = self.draw(transition)
        began = time.monotonic_ns()
        await asyncio.sleep(seconds)
        floor = time.monotonic_ns() - began
        if then is not None:
            await then()
        self.truth(credential, transition, seconds, time.monotonic_ns() - began, floor)

    async def handle(self, method: str, path: str, body: bytes):
        if path == "/clock":
            return 200, {"t": self.now()}
        parts = path.strip("/").split("/")
        route = getattr(self, f"on_{method.lower()}_{parts[0]}", None)
        if route is None or len(parts) != 2:
            return 404, {"error": f"There is no route {method} {path}."}
        return await route(parts[1], body)


class Issuer(Role):
    def __init__(self, name, spec, truth, state: str) -> None:
        super().__init__(name, spec, truth)
        self.state = state.rstrip("/")
        self.ops: dict[str, dict] = {}
        self.changed: dict[str, asyncio.Event] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.constructed = 0
        self.client = client_http.HttpClient(timeout_s=30)
        self.tasks: set[asyncio.Task] = set()

    def spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def mark(self, credential: str, milestone: str) -> None:
        self.ops[credential]["milestones"][milestone] = self.now()
        self.changed.setdefault(credential, asyncio.Event()).set()
        self.changed[credential] = asyncio.Event()

    async def worker(self) -> None:
        """Construct one credential per ``1/service_rate`` seconds while there is work.

        Each slot is due a fixed period after the previous one, never after the previous one's
        late wake-up, so sleep overshoot does not accumulate and a backlogged issuer runs at
        exactly the cap.
        """
        period = round(1e9 / self.spec["service_rate"])
        due = time.monotonic_ns()
        while True:
            credential = await self.queue.get()
            if self.spec.get("stall_after") is not None \
                    and self.constructed == self.spec["stall_after"]:
                await asyncio.sleep(self.spec["stall_ms"] / 1e3)
            due = max(due + period, time.monotonic_ns())
            await asyncio.sleep(max(0, due - time.monotonic_ns()) / 1e9)
            self.constructed += 1
            self.mark(credential, "constructed")
            self.spawn(self.finish(credential))

    async def finish(self, credential: str) -> None:
        for transition in ("constructed>registry_updated", "registry_updated>anchored",
                           "anchored>witnessed"):
            await self.delay(credential, transition)
            self.mark(credential, transition.split(">")[1])
        await self.delay(credential, "witnessed>published",
                         lambda: self.client.post(f"{self.state}/publish/{credential}"))

    async def withdraw(self, credential: str) -> None:
        for transition in ("revocation_requested>revocation_anchored",
                           "revocation_anchored>revocation_witnessed"):
            await self.delay(credential, transition)
            self.mark(credential, transition.split(">")[1])
        await self.delay(credential, "revocation_witnessed>revocation_published",
                         lambda: self.client.post(f"{self.state}/revoke/{credential}"))

    async def on_post_issue(self, credential: str, body: bytes):
        if credential not in self.ops:
            self.ops[credential] = {"said": f"E{credential}", "milestones": {}}
            self.queue.put_nowait(credential)
        return 202, {"operation": credential}

    async def on_post_revoke(self, credential: str, body: bytes):
        if credential not in self.ops:
            return 404, {"error": f"No credential {credential} was issued here."}
        self.spawn(self.withdraw(credential))
        return 202, {"operation": credential}

    async def on_get_operations(self, query: str, body: bytes):
        """``GET /operations/<credential>?until=<milestone>`` waits until the milestone exists."""
        credential, _, until = query.partition("?until=")
        if credential not in self.ops:
            return 404, {"error": f"No operation {credential} exists."}
        while until and until not in self.ops[credential]["milestones"]:
            await self.changed.setdefault(credential, asyncio.Event()).wait()
        return 200, self.ops[credential]

    async def on_get_anchors(self, credential: str, body: bytes):
        return 200, {"anchors": 1 if "anchored" in self.ops.get(credential, {}).get(
            "milestones", {}) else 0}


class StateDistribution(Role):
    def __init__(self, name, spec, truth) -> None:
        super().__init__(name, spec, truth)
        self.published: set[str] = set()
        self.revoked: set[str] = set()

    async def on_post_publish(self, credential: str, body: bytes):
        self.published.add(credential)
        return 200, {}

    async def on_post_revoke(self, credential: str, body: bytes):
        self.revoked.add(credential)
        return 200, {}

    async def on_get_status(self, credential: str, body: bytes):
        return 200, {"published": credential in self.published,
                     "revoked": credential in self.revoked}


class Holder(Role):
    async def on_post_deliver(self, credential: str, body: bytes):
        return 202, {}

    async def on_post_admit(self, credential: str, body: bytes):
        await self.delay(credential, "delivered>stored")
        return 200, {}

    async def on_post_present(self, credential: str, body: bytes):
        await self.delay(credential, "stored>proof_generated")
        return 200, {"presentation": f"P{credential}"}


class Verifier(Role):
    def __init__(self, name, spec, truth, state: str) -> None:
        super().__init__(name, spec, truth)
        self.state = state.rstrip("/")
        self.client = client_http.HttpClient(timeout_s=30)

    async def on_post_verify(self, credential: str, body: bytes):
        await self.delay(credential, "proof_generated>verified")
        return 200, {"valid": True}

    async def on_get_status(self, credential: str, body: bytes):
        answer = (await self.client.get(f"{self.state}/status/{credential}")).json()
        status = "revoked" if answer["revoked"] else "valid" if answer["published"] else "unknown"
        return 200, {"status": status, "said": f"E{credential}" if answer["published"] else None}

    async def on_post_sync(self, run: str, body: bytes):
        await self.delay(run, "sync_full_started>sync_full_done")
        return 200, {}


async def serve(args) -> None:
    spec = json.loads(args.spec)
    role = {"issuer": lambda: Issuer(args.role, spec, args.truth, args.state),
            "state_distribution": lambda: StateDistribution(args.role, spec, args.truth),
            "holder": lambda: Holder(args.role, spec, args.truth),
            "verifier": lambda: Verifier(args.role, spec, args.truth, args.state)}[args.role]()
    server = await http.serve(role.handle, args.port)
    if isinstance(role, Issuer):
        role.spawn(role.worker())
    port = server.sockets[0].getsockname()[1]
    print(json.dumps({"url": f"http://127.0.0.1:{port}"}), flush=True)
    async with server:
        await server.serve_forever()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=ROLES)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--state", default="")
    asyncio.run(serve(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
