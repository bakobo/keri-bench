"""Adapters for the imbu stack's other roles: heti's witnesses, the Registrar with heti's
Observer, a heti holder and an independent heti verifier.

Every process listens on a port the adapter chose, so a crash fault restarts it where its proxy
already points. Two links still bypass the proxy and are named rather than hidden: the
Registrar's batches to the Observer go to the callback address the Observer gives the Registrar
itself, and imbu's publisher and the Observer reach the witnesses at the addresses the witnesses'
location records name, which this module fronts with the proxy, so those do pass through.
"""

from __future__ import annotations

import asyncio
import base64
import json
import secrets
import socket
import sys
from collections.abc import Mapping
from typing import Any

import fiki  # the stacks extra; registry.lookup names it when it is missing

from ..adapters.base import (Declaration, HolderAdapter, Issued, Observation,
                             StateDistributionAdapter, VerifierAdapter, WitnessPoolAdapter)
from ..errors import BenchError
from ..http import Answer, HttpClient
from ..knobs import Knob, KnobSchema
from ..procs import free_port
from ..vocabulary import Role

VERSION = "2"
HETI = "3a8b1ccb17e1c29b79aafb191cb16cde35e2727b"
IMBU = "7ff15e6ada507c462a740633d5f16c92cc34470d"
WITNESS = "b12a25e293e46b8a0febe180ca19f38cdbeeec3e"
"""The bakobo/witness commit the Registrar runs from: main with the Registrar that takes a
publication without the issuer KEL (witness @23vjzsf0), which imbu sends since 670a3d8 (this.i
5k8z0pxe), at b12a25e, whose keripy pin answers a KEL query from a first-seen ordinal (this.i
0v5zd44x). It pins another keripy than heti does, so it runs in its own uvx environment, as the
summit runs it."""

REGISTRAR = ("uvx", "--from", f"witness @ git+https://github.com/bakobo/witness@{WITNESS}",
             "witness", "registrar")
PYTHON = sys.executable
STARTUP = 600.0
"""Seconds. The first Registrar start builds its uvx environment from git."""

E_STAGE = "e.party.stack.refused.f"


def passcode() -> str:
    """A fresh passcode for a lockbox that lives only as long as the run (heti wants 21+)."""
    return secrets.token_urlsafe(24)


async def listening(url: str, timeout: float = 30.0) -> None:
    """Wait until something accepts connections at ``url``: for a process that announces itself
    before its listener binds (the Registrar does)."""
    host, port = url.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        try:
            _, writer = await asyncio.open_connection(host, int(port))
            writer.close()
            return
        except OSError:
            if loop.time() > deadline:
                raise
            await asyncio.sleep(0.05)


def _check(answer: Answer, stage: str, credential: str) -> Answer:
    if answer.status >= 300:
        raise BenchError(E_STAGE, f"The stack refused {stage} for {credential} with "
                         f"{answer.status}: {answer.body[:300]!r}")
    return answer


class HetiWitnessPool(WitnessPoolAdapter):
    """Real keripy witnesses, each advertising the harness's proxy in front of it. It cannot
    yet count the KEL events anchoring a credential, so reconciliation's double-anchor check
    reads not measured here (~6j5i)."""

    declaration = Declaration(
        adapter="heti.witness_pool", role=Role.WITNESS_POOL, version=VERSION, milestones={},
        knobs=KnobSchema(1, {"count": Knob(int, "How many witnesses the pool runs.")}),
        tested_against={"heti": HETI})

    async def start(self):
        """One process per witness, ``witness_pool.w<i>``, so a crash can take one witness of
        the pool or all of them; each keeps its keystore under the run directory and derives
        its salt from one seed per run, so a restart is the same witness (this.i 5ed29j5w)."""
        seed = secrets.token_bytes(14).hex()
        oobis, aids = [], []
        for index in range(self.ctx.knobs["count"]):
            port = free_port()
            front = await self.ctx.front(f"w{index}", f"http://127.0.0.1:{port}")
            process = await self.ctx.procs.start(
                f"witness_pool.w{index}", (PYTHON, "-m", "keri_bench.stacks.witnesses"),
                ("--port", str(port), "--advertise", front, "--seed", seed, "--first",
                 str(index), "--store", str(self.ctx.run_dir / "witnesses")),
                timeout=STARTUP)
            oobis += process.announced["oobis"]
            aids += process.announced["aids"]
        return {"oobis": oobis, "aids": aids}

    async def versions(self):
        return {"heti": HETI}


class RegistrarObserver(StateDistributionAdapter):
    """bakobo/witness's Registrar, and heti's Observer subscribed to it: what verifiers read.

    The Registrar's batches go to the callback the Observer registers itself, past the proxy
    (~3zja)."""

    declaration = Declaration(
        adapter="registrar.state_distribution", role=Role.STATE_DISTRIBUTION, version=VERSION,
        milestones={},
        knobs=KnobSchema(1, {
            "window_s": Knob(float, "The Registrar's batching window.", unit="s"),
            "delivery_timeout_s": Knob(float, "How long the Registrar waits on a subscriber.",
                                       unit="s"),
            "resync_s": Knob(float, "How often the Observer resynchronizes; must exceed the "
                             "window.", unit="s"),
        }),
        tested_against={"witness": WITNESS, "heti": HETI})

    async def start(self):
        self.publisher = fiki.Key.generate()
        port = free_port()
        front = await self.ctx.front("registrar", f"http://127.0.0.1:{port}")
        knobs = self.ctx.knobs
        process = await self.ctx.procs.start(
            "state_distribution.registrar", REGISTRAR, (
                "--store", str(self.ctx.run_dir / "registrar"), "--host", "127.0.0.1",
                "--port", str(port), "--publisher", self.publisher.aid,
                "--window", f"{knobs['window_s']:g}", "--allow-callback", "127.0.0.0/8",
                "--delivery-timeout", f"{knobs['delivery_timeout_s']:g}"),
            timeout=STARTUP, ready=lambda announced: listening(f"http://127.0.0.1:{port}"))
        return {"registrar": front, "registrar_aid": process.announced["aid"],
                "publisher_seed": self.publisher.seed.hex()}

    async def connect(self):
        """The Observer needs the issuer's OOBI, so it starts once the issuer has."""
        home = self.ctx.run_dir / "observer"
        home.mkdir(mode=0o700, exist_ok=True)
        port = free_port()
        mine = self.ctx.peers[Role.STATE_DISTRIBUTION]
        args = ["--store", str(home / "observer.sqlite3"), "--lockbox", str(home / "lockbox"),
                "--registrar", mine["registrar"], "--registrar-aid", mine["registrar_aid"],
                "--resync", f"{self.ctx.knobs['resync_s']:g}", "--port", str(port)]
        for oobi in self.ctx.peers[Role.WITNESS_POOL]["oobis"]:
            args += ["--witness-oobi", oobi]
        # One OOBI per imbu home (imbu.ImbuIssuer.start), so the Observer knows every issuer.
        for oobi in self.ctx.peers[Role.ISSUER]["oobis"]:
            args += ["--issuer-oobi", oobi]
        process = await self.ctx.procs.start(
            "state_distribution.observer", (PYTHON, "-m", "heti.observer"), tuple(args),
            env={"HETI_OBSERVER_PASSCODE": passcode()}, timeout=STARTUP)
        return {"observer": process.announced["url"], "observer_aid": process.announced["aid"]}

    async def versions(self):
        return {"witness": WITNESS, "heti": HETI}


class _Party:
    """A heti holder or verifier process: a lockbox with one witnessed facet."""

    module: str
    process_name: str

    async def _spawn(self, extra: tuple[str, ...] = ()) -> dict:
        self.client = HttpClient()
        port = free_port()
        args = ["--lockbox", str(self.ctx.run_dir / self.process_name), "--port", str(port),
                "--toad", str(self.ctx.knobs["toad"]),
                "--spool", str(self.ctx.run_dir / f"{self.process_name}-spool")]
        for oobi in self.ctx.peers[Role.WITNESS_POOL]["oobis"]:
            args += ["--witness", oobi]
        for oobi in self.ctx.peers[Role.ISSUER]["oobis"]:
            args += ["--issuer-oobi", oobi]
        process = await self.ctx.procs.start(
            self.process_name, (PYTHON, "-m", self.module), tuple(args) + extra,
            env={"BENCH_PASSCODE": passcode()}, timeout=STARTUP)
        self.me = {"aid": process.announced["aid"], "oobi": process.announced["oobi"]}
        return {"api": process.announced["url"]}

    async def identity(self):
        return self.me

    def api(self) -> str:
        return self.ctx.peers[self.declaration.role]["api"]

    async def stop(self) -> None:
        client = getattr(self, "client", None)
        if client is not None:
            await client.close()

    async def versions(self):
        return {"heti": HETI}


class HetiHolder(_Party, HolderAdapter):
    module, process_name = "keri_bench.stacks.holder", "holder"
    declaration = Declaration(
        adapter="heti.holder", role=Role.HOLDER, version=VERSION,
        milestones={"delivered": Observation("observed", "harness"),
                    "stored": Observation("observed", "harness"),
                    "proof_generated": Observation("observed", "harness")},
        knobs=KnobSchema(1, {"toad": Knob(int, "Receipts the holder's facet requires.")}),
        tested_against={"heti": HETI})

    async def start(self):
        # The verifier's revocation check asks this holder to present again (HetiVerifier.status).
        self.ctx.shared["holder"] = self
        return {}

    async def connect(self):
        """After the Observer, which starts in the state-distribution role's connect, since a
        re-presentation reads the chain the Observer holds now."""
        observer = self.ctx.peers[Role.STATE_DISTRIBUTION]
        return await self._spawn(("--observer-url", observer["observer"], "--observer-aid",
                                  observer["observer_aid"]))

    async def represent(self, credential: str, verifier: Mapping[str, Any]) -> dict:
        """A fresh presentation on the Observer's current chain, for a revocation check; not a
        stage, so it emits nothing."""
        answer = _check(await self.client.post(
            f"{self.api()}/represent/{credential}",
            body=json.dumps({"verifier": verifier["aid"]}).encode()), "re-presentation",
            credential)
        return {"grant": answer.json()["grant"], "oobi": self.me["oobi"]}

    async def receive(self, issued: Issued) -> None:
        credential = issued.credential
        _check(await self.client.post(f"{self.api()}/deliver/{credential}", body=issued.grant),
               "delivery", credential)
        self.ctx.emit("delivered", credentials=[credential])
        _check(await self.client.post(f"{self.api()}/admit/{credential}"), "admission",
               credential)
        self.ctx.emit("stored", credentials=[credential])

    async def present(self, credential: str, verifier: Mapping[str, Any]):
        answer = _check(await self.client.post(
            f"{self.api()}/present/{credential}",
            body=json.dumps({"verifier": verifier["aid"]}).encode()), "presentation",
            credential)
        self.ctx.emit("proof_generated", credentials=[credential])
        return {"grant": answer.json()["grant"], "oobi": self.me["oobi"]}


class HetiVerifier(_Party, VerifierAdapter):
    # sync_increment_applied is not observed: pair 5's incremental half is a named gap (~6ayr).
    module, process_name = "keri_bench.stacks.verifier", "verifier"
    declaration = Declaration(
        adapter="heti.verifier", role=Role.VERIFIER, version=VERSION,
        milestones={"verified": Observation("observed", "harness"),
                    "sync_full_started": Observation("observed", "harness"),
                    "sync_full_done": Observation("observed", "harness")},
        knobs=KnobSchema(1, {
            "toad": Knob(int, "Receipts the verifier's facet requires."),
            "freshness_s": Knob(float, "How old the Observer's state may be before the verifier "
                                "refuses to rely on it.", unit="s"),
            "retry_ms": Knob(float, "How soon a refused verification is tried again.",
                             unit="ms"),
            "deadline_s": Knob(float, "How long verification is retried before the credential "
                               "counts as not verified.", unit="s"),
        }),
        tested_against={"heti": HETI})

    async def start(self):
        return {}

    async def connect(self):
        """The Observer starts in the state-distribution role's connect, which runs first."""
        observer = self.ctx.peers[Role.STATE_DISTRIBUTION]
        return await self._spawn(("--observer-url", observer["observer"], "--observer-aid",
                                  observer["observer_aid"], "--freshness",
                                  f"{self.ctx.knobs['freshness_s']:g}"))

    async def verify(self, credential: str, presentation) -> bool:
        """Verify, retrying refusals until the deadline: a presentation can reach the verifier
        before the Observer holds the state that makes it current, and a real verifier asked
        again later would accept it. ``verified`` is the first acceptance, so the interval from
        ``proof_generated`` includes waiting for state to arrive, which is what independent
        verification costs (pair 3). The number of attempts goes with the event."""
        knobs = self.ctx.knobs
        deadline = self.ctx.now() + round(knobs["deadline_s"] * 1e9)
        body, attempts = json.dumps(presentation).encode(), 0
        while True:
            attempts += 1
            answer = await self.client.post(f"{self.api()}/verify/{credential}", body=body)
            if answer.status < 300 or self.ctx.now() >= deadline:
                break
            await asyncio.sleep(knobs["retry_ms"] / 1e3)
        _check(answer, "verification", credential)
        found = answer.json()
        said = self.ctx.shared.get("said", {}).get(credential)
        if not isinstance(found, dict) or found.get("valid") is not True \
                or not isinstance(said, str) or not said or found.get("said") != said:
            raise BenchError(E_STAGE, f"The verifier answered {answer.status} for {credential} "
                             f"without accepting it as issued: {found!r}"[:500])
        self.ctx.emit("verified", credentials=[credential], attrs={"attempts": attempts})
        return True

    async def status(self, credential: str):
        """Asks the holder to present again and judges that presentation: on a blinded registry
        the first presentation's disclosures cannot read a later revocation (holder.py), so a
        poll here costs a holder round trip, and revocation_recognised is when a fresh
        presentation is refused as revoked."""
        said = self.ctx.shared.get("said", {}).get(credential)
        if said is None:
            return {"status": "unknown", "said": None}
        try:
            fresh = await self.ctx.shared["holder"].represent(credential, self.me)
        except BenchError:
            return {"status": "unknown", "said": said}
        answer = await self.client.post(f"{self.api()}/status/{said}",
                                        body=json.dumps(fresh).encode())
        _check(answer, "status", credential)
        return {"status": answer.json()["status"], "said": said}

    async def sync_cold(self) -> None:
        self.ctx.emit("sync_full_started", credentials=["sync-cold"])
        _check(await self.client.post(f"{self.api()}/sync/cold"), "a cold sync", "sync-cold")
        self.ctx.emit("sync_full_done", credentials=["sync-cold"])
