"""Adapters for the calibration stack. They use every observation path a real adapter uses: an
API boundary (observed), the proxy (observed), and stack-written times on a skewed clock
(reported), so a calibration run exercises the harness's whole measurement chain."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Any

from ..adapters.base import (HolderAdapter, IssuerAdapter, Issued, Observation, Declaration,
                             StateDistributionAdapter, VerifierAdapter, WitnessPoolAdapter)
from ..errors import BenchError
from ..http import Answer, HttpClient
from ..knobs import Knob, KnobSchema
from ..procs import free_port
from ..vocabulary import Role

VERSION = "1"
SERVER = (sys.executable, "-m", "keri_bench.synthetic.server")
ISSUER_CLOCK = "stack:synthetic-issuer"
REPORTED_RESOLUTION = 1_000
"""The synthetic issuer stamps with time.time_ns(); a microsecond is a fair bound on what the
stamp means once a Python task has been scheduled."""

E_DELAYS = "e.input.format.scenario.knob.delays.f"
E_STAGE = "e.state.pending.synthetic.f"

COMMON = {
    "seed": Knob(int, "Seed for every injected delay."),
    "delays": Knob(str, "Injected delays, 'start>end=median_ms/sigma' separated by commas."),
    "clock_skew_ms": Knob(float, "How far the role's stack clock is from wall time.", unit="ms"),
}


def parse_delays(text: str) -> dict[str, list[float]]:
    delays = {}
    for item in filter(None, (part.strip() for part in text.split(","))):
        try:
            transition, spec = item.split("=")
            median, sigma = spec.split("/")
            if transition.count(">") != 1:
                raise ValueError
            delays[transition.strip()] = [float(median), float(sigma)]
        except ValueError:
            raise BenchError(E_DELAYS, f"The delay {item!r} is not 'start>end=median_ms/sigma'."
                             ) from None
    return delays


def _spec(knobs: Mapping[str, Any], **extra: Any) -> str:
    return json.dumps({"seed": knobs["seed"], "clock_skew_ms": knobs["clock_skew_ms"],
                       "delays": parse_delays(knobs["delays"]), **extra})


class _Synthetic:
    role_name: str

    async def _spawn(self, state: str = "") -> str:
        # A fixed port, so a crashed role restarts where its proxy points.
        args = (self.role_name, "--spec", self._spec(), "--truth",
                str(self.ctx.run_dir / "truth.jsonl"), "--port", str(free_port()))
        if state:
            args += ("--state", state)
        process = await self.ctx.procs.start(self.role_name, SERVER, args)
        self.url = process.announced["url"]
        self.client = HttpClient()
        return self.url

    def _spec(self) -> str:
        return _spec(self.ctx.knobs)

    async def stop(self) -> None:
        client = getattr(self, "client", None)
        if client is not None:
            await client.close()

    async def versions(self) -> dict[str, str]:
        return {"synthetic": VERSION}

    def api(self, role: Role) -> str:
        return self.ctx.peers[role]["api"]


def _check(answer: Answer, stage: str, credential: str) -> Answer:
    if answer.status >= 300:
        raise BenchError(E_STAGE, f"The synthetic stack answered {answer.status} to {stage} "
                         f"for {credential}.")
    return answer


class SyntheticIssuer(_Synthetic, IssuerAdapter):
    role_name = "issuer"
    declaration = Declaration(
        adapter="synthetic.issuer", role=Role.ISSUER, version=VERSION,
        milestones={
            "accepted": Observation("observed", "harness"),
            "constructed": Observation("reported", ISSUER_CLOCK, REPORTED_RESOLUTION),
            "registry_updated": Observation("reported", ISSUER_CLOCK, REPORTED_RESOLUTION),
            "anchored": Observation("reported", ISSUER_CLOCK, REPORTED_RESOLUTION),
            "witnessed": Observation("reported", ISSUER_CLOCK, REPORTED_RESOLUTION),
            "revocation_requested": Observation("observed", "harness"),
            "revocation_anchored": Observation("reported", ISSUER_CLOCK, REPORTED_RESOLUTION),
            "revocation_witnessed": Observation("reported", ISSUER_CLOCK, REPORTED_RESOLUTION),
        },
        knobs=KnobSchema(1, {**COMMON,
                             "service_rate": Knob(float, "Constructions per second: the cap.",
                                                  unit="1/s"),
                             "stall_after": Knob(int, "Stall once after this many "
                                                 "constructions.", allow_na=True),
                             "stall_ms": Knob(float, "How long the stall lasts.", allow_na=True,
                                              unit="ms")}),
        tested_against={"synthetic": VERSION})

    def _spec(self) -> str:
        knobs = self.ctx.knobs
        stall = knobs["stall_after"] if knobs["stall_after"] != "n/a" else None
        return _spec(knobs, service_rate=knobs["service_rate"], stall_after=stall,
                     stall_ms=knobs["stall_ms"] if stall is not None else 0.0)

    async def start(self):
        return {"api": await self._spawn(state=self.api(Role.STATE_DISTRIBUTION))}

    async def _until(self, credential: str, milestone: str) -> dict:
        answer = await self.client.get(f"{self.api(Role.ISSUER)}/operations/{credential}"
                                       f"?until={milestone}")
        return _check(answer, f"waiting for {milestone}", credential).json()

    async def issue(self, credential: str, holder: Mapping[str, Any]) -> Issued:
        answer = await self.client.post(f"{self.api(Role.ISSUER)}/issue/{credential}")
        _check(answer, "issue", credential)
        self.ctx.emit("accepted", credentials=[credential])
        op = await self._until(credential, "witnessed")
        for name in ("constructed", "registry_updated", "anchored", "witnessed"):
            self.ctx.emit(name, credentials=[credential], t=op["milestones"][name],
                          attrs={"said": op["said"]})
        return Issued(credential, op["said"], f"G{credential}".encode(), False)

    async def revoke(self, credential: str) -> None:
        self.ctx.emit("revocation_requested", credentials=[credential])
        _check(await self.client.post(f"{self.api(Role.ISSUER)}/revoke/{credential}"),
               "revoke", credential)
        op = await self._until(credential, "revocation_witnessed")
        for name in ("revocation_anchored", "revocation_witnessed"):
            self.ctx.emit(name, credentials=[credential], t=op["milestones"][name])

    def clock_sources(self):
        async def read() -> int:
            return (await self.client.get(f"{self.api(Role.ISSUER)}/clock")).json()["t"]
        return {ISSUER_CLOCK: read}


class SyntheticStateDistribution(_Synthetic, StateDistributionAdapter):
    role_name = "state_distribution"
    declaration = Declaration(
        adapter="synthetic.state_distribution", role=Role.STATE_DISTRIBUTION, version=VERSION,
        milestones={"published": Observation("observed", "harness"),
                    "revocation_published": Observation("observed", "harness")},
        knobs=KnobSchema(1, dict(COMMON)), tested_against={"synthetic": VERSION})

    async def start(self):
        return {"api": await self._spawn()}

    async def connect(self):
        # The issuer reaches this role only through the proxy, so the proxy's record of a 2xx
        # answer to a publication is the moment it was published.
        self.ctx.observe("state_distribution.api", self._seen)
        return {}

    def _seen(self, exchange) -> None:
        if exchange.method != "POST" or exchange.status != 200 or exchange.duplicate:
            return
        kind, _, credential = exchange.path.strip("/").partition("/")
        name = {"publish": "published", "revoke": "revocation_published"}.get(kind)
        if name is not None:
            self.ctx.emit(name, credentials=[credential], t=exchange.t_response)


class SyntheticWitnessPool(WitnessPoolAdapter):
    """The calibration stack's witnessing happens inside its issuer; this role only answers
    reconciliation's anchor count."""

    declaration = Declaration(
        adapter="synthetic.witness_pool", role=Role.WITNESS_POOL, version=VERSION,
        milestones={}, knobs=KnobSchema(1, {}), tested_against={"synthetic": VERSION})

    async def anchors(self, credential: str) -> int | None:
        if not hasattr(self, "client"):
            self.client = HttpClient(timeout_s=30)
        answer = await self.client.get(f"{self.ctx.peers[Role.ISSUER]['api']}/anchors/"
                                       f"{credential}")
        return answer.json()["anchors"]

    async def stop(self) -> None:
        if hasattr(self, "client"):
            await self.client.close()


class SyntheticHolder(_Synthetic, HolderAdapter):
    role_name = "holder"
    declaration = Declaration(
        adapter="synthetic.holder", role=Role.HOLDER, version=VERSION,
        milestones={"delivered": Observation("observed", "harness"),
                    "stored": Observation("observed", "harness"),
                    "proof_generated": Observation("observed", "harness")},
        knobs=KnobSchema(1, dict(COMMON)), tested_against={"synthetic": VERSION})

    async def start(self):
        return {"api": await self._spawn()}

    async def identity(self):
        return {"aid": "Hsynthetic"}

    async def receive(self, issued: Issued) -> None:
        api, credential = self.api(Role.HOLDER), issued.credential
        _check(await self.client.post(f"{api}/deliver/{credential}", body=issued.grant),
               "deliver", credential)
        self.ctx.emit("delivered", credentials=[credential])
        _check(await self.client.post(f"{api}/admit/{credential}"), "admit", credential)
        self.ctx.emit("stored", credentials=[credential])

    async def present(self, credential: str, verifier: Mapping[str, Any]):
        answer = _check(await self.client.post(f"{self.api(Role.HOLDER)}/present/{credential}"),
                        "present", credential)
        self.ctx.emit("proof_generated", credentials=[credential])
        return answer.json()["presentation"]


class SyntheticVerifier(_Synthetic, VerifierAdapter):
    role_name = "verifier"
    declaration = Declaration(
        adapter="synthetic.verifier", role=Role.VERIFIER, version=VERSION,
        milestones={"verified": Observation("observed", "harness"),
                    "sync_full_started": Observation("observed", "harness"),
                    "sync_full_done": Observation("observed", "harness")},
        knobs=KnobSchema(1, dict(COMMON)), tested_against={"synthetic": VERSION})

    async def start(self):
        return {"api": await self._spawn(state=self.api(Role.STATE_DISTRIBUTION))}

    async def identity(self):
        return {"aid": "Vsynthetic"}

    async def verify(self, credential: str, presentation) -> bool:
        answer = _check(await self.client.post(f"{self.api(Role.VERIFIER)}/verify/{credential}",
                                               body=str(presentation).encode()),
                        "verify", credential)
        if answer.json().get("valid") is not True:
            return False
        self.ctx.emit("verified", credentials=[credential])
        return True

    async def status(self, credential: str):
        return _check(await self.client.get(f"{self.api(Role.VERIFIER)}/status/{credential}"),
                      "status", credential).json()

    async def sync_cold(self) -> None:
        self.ctx.emit("sync_full_started", credentials=["sync-cold"])
        _check(await self.client.post(f"{self.api(Role.VERIFIER)}/sync/cold"), "sync",
               "sync-cold")
        self.ctx.emit("sync_full_done", credentials=["sync-cold"])
