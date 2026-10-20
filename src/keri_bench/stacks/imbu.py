"""The imbu issuer adapter, against imbu's asynchronous operations (imbu 963dfec, plan item
imbu-async, decision Q-EVAG).

A mutating request answers 202 with ``Location: /v1/imbu/operations/{id}`` and the operation in
the body; the operation records each milestone it owes with the time it happened, stamped by
whatever observed it rather than by whoever polls (imbu src/imbu/operations.py). This adapter
reads those times as ``reported`` events. It has no synchronous fallback, on purpose: a
synchronous issuance answers once witnessing is done, so anchored and witnessed could only be
inferred, and an inferred milestone is never reported (this.i 7wt73dlu).

What imbu's operation carries, as this adapter reads it:
- the operation's id is the 202 body's ``id``, or the last segment of its Location header;
- milestones are ``op["milestones"][name]``, RFC 3339 UTC with microseconds, on imbu's wall
  clock, which on the same host is the harness's ``realtime`` domain with a measured offset; a
  milestone owed and not yet reached is null;
- an issuance owes accepted, anchored, witnessed, published (when a publisher is wired) and
  finished; imbu stamps no ``constructed`` or ``registry_updated``, so neither is declared here;
- the operation is finished when ``state`` is ``done``, and failed when it is ``failed``, with
  ``result.said`` and ``result.grant`` (base64) on success;
- the client's idempotency key travels as the ``Idempotency-Key`` header.

Each issuance through ``/v1/imbu/issuances`` gets a registry of its own (imbu @2tdzyh85, Daniel's
Q-ZSCW), as SEDI's pool does, so every credential verifies independently against the Observer.

The ``homes`` knob runs that many imbu issuers, each its own process and home, sharing the run's
witnesses, Registrar, Observer, holder and verifier; ``clients`` gives each home that many
controllers, each signing with its own key over its own connection pool. Credentials are dealt
to homes round-robin and then, within a home, to its clients round-robin; a revocation goes to
the client that issued (this.i 2lkqz9mq).
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fiki  # the stacks extra; registry.lookup names it when it is missing

from ..adapters.base import Declaration, IssuerAdapter, Issued, Observation
from ..errors import BenchError
from ..http import Answer, HttpClient
from ..knobs import Knob, KnobSchema
from ..procs import free_port
from ..vocabulary import Role
from .adapters import IMBU, PYTHON, STARTUP, passcode

VERSION = "2"
WALL = 1_000
"""imbu stamps milestones in RFC 3339 with microseconds (imbu operations.now), so a microsecond
is the resolution claimed for them."""

ISSUE_MILESTONES = ("anchored", "witnessed", "published")
REVOKE_MILESTONES = {"anchored": "revocation_anchored", "witnessed": "revocation_witnessed",
                     "published": "revocation_published"}

E_REFUSED = "e.party.imbu.refused.f"
E_FAILED = "e.party.imbu.operation.f"
E_SHAPE = "e.input.format.imbu.operation.f"
E_TIMEOUT = "e.env.imbu.operation.r"
E_DEPLOYMENT = "e.input.range.imbu.deployment.f"


def stamp(value: Any) -> int:
    """A milestone time as integer nanoseconds: an integer as given, or ISO 8601."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            moment = datetime.datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            if moment.tzinfo is not None:
                epoch = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC)
                delta = moment - epoch
                return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 \
                    + delta.microseconds * 1_000
    raise BenchError(E_SHAPE, f"imbu stamped a milestone {value!r}, which is neither integer "
                     f"nanoseconds nor an ISO 8601 time with a zone.")


def operation_id(answer: Answer) -> str:
    try:
        body = answer.json()
    except ValueError:
        body = {}
    found = (body.get("operation") or body.get("id")) if isinstance(body, dict) else None
    if isinstance(found, dict):
        found = found.get("id")
    if not found and "Location" in answer.headers:
        found = answer.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
    if not found:
        raise BenchError(E_SHAPE, f"imbu answered 202 without naming the operation: {body!r}"
                         [:300])
    return str(found)


@dataclass
class Client:
    """One controller of a home: its key, and a connection pool of its own."""

    key: Any
    http: HttpClient = field(default_factory=HttpClient)


@dataclass
class Home:
    index: int
    control: str = ""
    clients: list[Client] = field(default_factory=list)


class ImbuIssuer(IssuerAdapter):
    declaration = Declaration(
        adapter="imbu.issuer", role=Role.ISSUER, version=VERSION,
        milestones={
            "accepted": Observation("observed", "harness"),
            **{name: Observation("reported", "realtime", WALL) for name in ISSUE_MILESTONES},
            "revocation_requested": Observation("observed", "harness"),
            **{name: Observation("reported", "realtime", WALL)
               for name in REVOKE_MILESTONES.values()},
        },
        knobs=KnobSchema(2, {
            "toad": Knob(int, "Witness receipts imbu requires before an event is settled."),
            "poll_ms": Knob(float, "How often the adapter reads an operation until it finishes; "
                            "the milestone times come from imbu, not from this poll.",
                            unit="ms"),
            "operation_timeout_s": Knob(float, "How long an operation may run before the "
                                        "adapter gives up on it.", unit="s"),
            "homes": Knob(int, "How many imbu issuers, each its own home and process, share "
                          "the deployment (this.i 2lkqz9mq)."),
            "clients": Knob(int, "How many controllers each home serves, each with its own key "
                            "and connection pool."),
        }),
        tested_against={"imbu": IMBU})

    OPERATOR_ACTS = ("create {record, commitment};destroy commitment;create relationship;"
                     "observe info")

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.homes: list[Home] = []
        self.dealt = 0
        self.owner: dict[str, tuple[Home, Client]] = {}

    async def start(self):
        """Each home is imbu in its own process with the Registrar publisher wired
        (stacks/issuer.py), under ``<run_dir>/imbu/h<i>``, named ``issuer.h<i>``."""
        homes, clients = self.ctx.knobs["homes"], self.ctx.knobs["clients"]
        if homes < 1 or clients < 1:
            raise BenchError(E_DEPLOYMENT, f"The imbu issuer needs at least one home and one "
                             f"client per home, not {homes} and {clients}.")
        state = self.ctx.peers[Role.STATE_DISTRIBUTION]
        found = []
        for index in range(homes):
            home = Home(index, clients=[Client(fiki.Key.generate()) for _ in range(clients)])
            args = ["--toad", str(self.ctx.knobs["toad"]), "--registrar-url",
                    state["registrar"], "--control-port", str(free_port())]
            for client in home.clients:
                args += ["--controller", f"{client.key.aid}={self.OPERATOR_ACTS}"]
            for oobi in self.ctx.peers[Role.WITNESS_POOL]["oobis"]:
                args += ["--witness", oobi]
            process = await self.ctx.procs.start(
                f"issuer.h{index}", (PYTHON, "-m", "keri_bench.stacks.issuer"), tuple(args),
                env={"BENCH_PASSCODE": passcode(),
                     "IMBU_HOME": str(self.ctx.run_dir / "imbu" / f"h{index}"),
                     "BENCH_PUBLISHER_SEED": state["publisher_seed"]}, timeout=STARTUP)
            self.homes.append(home)
            found.append(process.announced)
        # One OOBI per home: what the Observer, holder and verifier resolve to know each issuer.
        return {"control": [f["control"] for f in found],
                "oobis": [f["oobis"][0] for f in found],
                "issuer": [f["issuer"] for f in found]}

    async def connect(self):
        for home, control in zip(self.homes, self.ctx.peers[Role.ISSUER]["control"]):
            home.control = control
        return {}

    async def versions(self):
        return {"imbu": IMBU}

    async def footprint(self) -> int:
        """Bytes under every home's directory: imbu's operation store and heti's lockbox."""
        total = 0
        for root, _, files in os.walk(Path(self.ctx.run_dir) / "imbu"):
            for name in files:
                total += os.path.getsize(os.path.join(root, name))
        return total

    def _deal(self, credential: str) -> tuple[Home, Client]:
        n = self.dealt
        self.dealt += 1
        home = self.homes[n % len(self.homes)]
        client = home.clients[(n // len(self.homes)) % len(home.clients)]
        self.owner[credential] = (home, client)
        return home, client

    @staticmethod
    def _where(home: Home, client: Client) -> dict:
        return {"home": home.index, "client": home.clients.index(client)}

    async def _call(self, home: Home, client: Client, method: str, path: str,
                    payload: Mapping | None = None, key: str | None = None) -> Answer:
        url = f"{home.control}{path}"
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {**fiki.sign_request(key=client.key, method=method, url=url, body=body),
                   "Content-Type": "application/json"}
        if key is not None:
            headers["Idempotency-Key"] = key
        return await client.http.request(method, url, body=body, headers=headers)

    async def _finish(self, home: Home, client: Client, operation: str) -> dict:
        knobs = self.ctx.knobs
        deadline = self.ctx.now() + round(knobs["operation_timeout_s"] * 1e9)
        while True:
            answer = await self._call(home, client, "GET", f"/v1/imbu/operations/{operation}")
            if answer.status != 200:
                raise BenchError(E_REFUSED, f"imbu answered {answer.status} when asked for "
                                 f"operation {operation}: {answer.body[:300]!r}")
            op = answer.json()
            if op.get("state") == "done":
                return op
            if op.get("state") == "failed":
                raise BenchError(E_FAILED, f"imbu's operation {operation} failed: "
                                 f"{op.get('error')!r}"[:500])
            if self.ctx.now() >= deadline:
                raise BenchError(E_TIMEOUT, f"imbu's operation {operation} had not finished "
                                 f"after {knobs['operation_timeout_s']} s.")
            await asyncio.sleep(knobs["poll_ms"] / 1e3)

    def _report(self, op: dict, credential: str, names: Mapping[str, str],
                attrs: dict) -> None:
        """Report every milestone a finished operation owes. One missing or null is refused rather
        than skipped: a done operation has reached them all, and a missing stamp would quietly
        drop the evidence a measurement needs."""
        milestones = op.get("milestones")
        if not isinstance(milestones, dict):
            raise BenchError(E_SHAPE, f"imbu's finished operation carries milestones "
                             f"{milestones!r}, not an object."[:500])
        for theirs in names:
            if milestones.get(theirs) is None:
                raise BenchError(E_SHAPE, f"imbu's finished operation did not stamp {theirs}, "
                                 f"which imbu owes for every anchoring verb when a publisher is "
                                 f"wired, as this stack's always is (issuer.py).")
        # All read before any is emitted, so a refused operation leaves nothing in the timeline.
        stamps = {ours: stamp(milestones[theirs]) for theirs, ours in names.items()}
        for ours, t in stamps.items():
            self.ctx.emit(ours, credentials=[credential], t=t, attrs=attrs)

    async def meet(self, holder: Mapping[str, Any]) -> None:
        """Every home resolves the holder's OOBI, so it holds the issuee's key state when it
        grants: heti seals a blinded registry's salt to the issuee only then (heti
        ipex._handoff), and without the salt the holder cannot disclose a later revocation
        (holder.py)."""
        for home in self.homes:
            client = home.clients[0]
            answer = await self._call(home, client, "POST", "/v1/imbu/resolutions",
                                      {"oobi": holder["oobi"]}, key=f"meet-{holder['aid']}")
            if answer.status != 202:
                raise BenchError(E_REFUSED, f"imbu answered {answer.status} to resolving the "
                                 f"holder, not 202: {answer.body[:300]!r}")
            resolved = (await self._finish(home, client, operation_id(answer))).get("result")
            if not isinstance(resolved, dict) or resolved.get("aid") != holder["aid"]:
                raise BenchError(E_SHAPE, f"imbu resolved the holder's OOBI to {resolved!r}, "
                                 f"not the holder {holder['aid']!r}."[:500])

    async def issue(self, credential: str, holder: Mapping[str, Any]) -> Issued:
        home, client = self._deal(credential)
        where = self._where(home, client)
        answer = await self._call(home, client, "POST", "/v1/imbu/issuances",
                                  {"recipient": holder["aid"],
                                   "attributes": {"benchCredential": credential}},
                                  key=credential)
        if answer.status != 202:
            raise BenchError(E_REFUSED, f"imbu answered {answer.status} to an issuance, not "
                             f"202: {answer.body[:300]!r}")
        self.ctx.emit("accepted", credentials=[credential], attrs=where)
        op = await self._finish(home, client, operation_id(answer))
        result = op.get("result") or {}
        said = result.get("said")
        self._report(op, credential, {name: name for name in ISSUE_MILESTONES},
                     {**where, "said": said} if said else where)
        self.ctx.shared.setdefault("said", {})[credential] = said
        grant = base64.b64decode(result["grant"]) if result.get("grant") else None
        return Issued(credential, said, grant, delivered_by_stack=False)

    async def revoke(self, credential: str) -> None:
        home, client = self.owner[credential]
        self.ctx.emit("revocation_requested", credentials=[credential])
        answer = await self._call(home, client, "POST", "/v1/imbu/revocations",
                                  {"said": self.ctx.shared["said"][credential]},
                                  key=f"revoke-{credential}")
        if answer.status != 202:
            raise BenchError(E_REFUSED, f"imbu answered {answer.status} to a revocation, not "
                             f"202: {answer.body[:300]!r}")
        self._report(await self._finish(home, client, operation_id(answer)), credential,
                     REVOKE_MILESTONES, self._where(home, client))

    async def stop(self) -> None:
        for home in self.homes:
            for client in home.clients:
                await client.http.close()
