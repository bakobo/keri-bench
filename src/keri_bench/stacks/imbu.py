"""The imbu issuer adapter, against imbu's asynchronous operations (imbu 670a3d8, plan item
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
- the client's idempotency key travels as the ``Idempotency-Key`` header;
- ``Prefer: wait=<s>`` (imbu @8f8e7hkm) holds a mutating request's or an operation read's answer
  for up to 25 s until the operation settles, so a client that waits needs one signed answer per
  operation instead of one per poll; the ``wait_s`` knob sends it, and ``poll_ms`` polls instead;
- each controller has a budget of signed answers (imbu @6u3jm9e4), set by the ``answer_rate``
  and ``answer_burst`` knobs; past it imbu answers 429 unsigned with ``Retry-After``, and the
  adapter asks again after that many seconds with the same request and key (this.i 5k8z0pxe).

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
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fiki  # the stacks extra; registry.lookup names it when it is missing
from keri.core.signing import Salter

from ..adapters.base import Declaration, IssuerAdapter, Issued, Observation
from ..errors import BenchError
from ..http import Answer, HttpClient
from ..knobs import Knob, KnobSchema
from ..procs import free_port
from ..vocabulary import Role
from .adapters import IMBU, PYTHON, STARTUP, passcode

VERSION = "4"
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
E_SIGNER = "e.input.range.imbu.signer.f"
E_SIGNER_RECORD = "e.input.format.imbu.signer.f"
E_WAIT = "e.input.range.imbu.wait.f"
E_BUDGET = "e.input.range.imbu.budget.f"
NA = "n/a"
WAIT_LIMIT = 25
"""The longest imbu holds a waiting answer, in seconds (imbu control/routes.py WAIT_LIMIT)."""
BURST_LIMIT = 2**32
"""The largest answer burst imbu accepts (imbu control/budget.py MAX_ANSWER_BURST)."""
sleep = asyncio.sleep
"""How the adapter waits out a Retry-After; a name of its own so a test need not wait."""


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
    citizen: int | None = None
    """For the bulk ceremony, which citizen's SMAID signs (stacks/bulk.py); None for a key."""


@dataclass
class Home:
    index: int
    control: str = ""
    clients: list[Client] = field(default_factory=list)
    operator: Client | None = None
    agent: dict | None = None
    """The bulk ceremony's operator, and the Utah agent credential this home announced."""
    signer_port: int | None = None
    signed: int = 0
    """Answers imbu signed with the principal's key: one signature each (this.i 2p26rn9h)."""
    refused: int = 0
    """Answers imbu refused unsigned because the controller's budget was spent (5k8z0pxe)."""
    issuances: int = 0


def check_signer(knobs) -> None:
    """Refuse a signer the adapter cannot build, before any process starts."""
    kind, latency, rate = knobs["signer"], knobs["signer_latency_ms"], knobs["signer_rate"]
    if kind == "lockbox" and (latency, rate) == (NA, NA):
        return
    if kind == "external" and (latency == NA or 0 <= latency < math.inf) and \
            (rate == NA or 0 < rate < math.inf):
        return
    raise BenchError(E_SIGNER, f"The imbu issuer's signer is {kind!r} with latency {latency!r} "
                     f"ms and rate {rate!r}; it must be lockbox with both n/a, or external with "
                     f"a latency that is n/a or finite and not negative, and a rate that is n/a or finite and "
                     f"positive.")


def check_waiting(knobs) -> None:
    """Refuse a client that neither polls nor waits, or does both, or waits for a time imbu
    cannot be asked for: whole seconds from 1 to imbu's bound."""
    poll, wait = knobs["poll_ms"], knobs["wait_s"]
    if wait == NA and poll != NA and 0 < poll < math.inf:
        return
    if poll == NA and wait != NA and 1 <= wait <= WAIT_LIMIT and wait == int(wait):
        return
    raise BenchError(E_WAIT, f"The imbu issuer polls every {poll!r} ms and waits {wait!r} s; "
                     f"exactly one of them must be n/a, a poll must be finite and positive, and "
                     f"a wait must be whole seconds from 1 to {WAIT_LIMIT}.")


def check_budget(knobs) -> None:
    """Refuse an answer budget imbu would refuse at startup, before any process starts."""
    rate, burst = knobs["answer_rate"], knobs["answer_burst"]
    if 0 < rate < math.inf and 1 <= burst <= BURST_LIMIT:
        return
    raise BenchError(E_BUDGET, f"The imbu issuer's answer budget is {rate!r} a second with a "
                     f"burst of {burst!r}; the rate must be finite and positive and the burst "
                     f"from 1 to {BURST_LIMIT}.")


def budget_args(knobs) -> list[str]:
    """Each home's answer budget, as the issuer process takes it (stacks/issuer.py)."""
    return ["--answer-rate", str(knobs["answer_rate"]), "--answer-burst",
            str(knobs["answer_burst"])]


def retry_after(answer: Answer) -> int | None:
    """A refusal's Retry-After as whole seconds, or None when it is missing or unreadable."""
    for name, value in answer.headers.items():
        if name.lower() == "retry-after":
            return int(value) if value.isascii() and value.isdigit() else None
    return None


def operation_document(answer: Answer) -> dict | None:
    """The operation a 202 carries in its body, if it carries one."""
    try:
        body = answer.json()
    except ValueError:
        return None
    if isinstance(body, dict) and isinstance(body.get("operation"), dict):
        return body["operation"]
    return body if isinstance(body, dict) else None


def signer_args(knobs, *, port: int) -> list[str]:
    """The issuer process's signer arguments (stacks/issuer.py); none for the lockbox."""
    if knobs["signer"] != "external":
        return []
    args = ["--signer", "external", "--signer-port", str(port)]
    if knobs["signer_latency_ms"] != NA:
        args += ["--signer-latency-ms", str(knobs["signer_latency_ms"])]
    if knobs["signer_rate"] != NA:
        args += ["--signer-rate", str(knobs["signer_rate"])]
    return args


class ImbuIssuer(IssuerAdapter):
    declaration = Declaration(
        adapter="imbu.issuer", role=Role.ISSUER, version=VERSION,
        milestones={
            **{name: Observation("reported", "realtime", WALL)
               for name in ("accepted", *ISSUE_MILESTONES)},
            "revocation_requested": Observation("observed", "harness"),
            **{name: Observation("reported", "realtime", WALL)
               for name in REVOKE_MILESTONES.values()},
        },
        knobs=KnobSchema(4, {
            "toad": Knob(int, "Witness receipts imbu requires before an event is settled."),
            "poll_ms": Knob(float, "How often the adapter reads an operation until it finishes; "
                            "the milestone times come from imbu, not from this poll. n/a when "
                            "the adapter waits instead.", allow_na=True, unit="ms"),
            "wait_s": Knob(float, "Prefer: wait sent on each request and operation read, so imbu "
                           "answers once the operation settles; n/a when the adapter polls.",
                           allow_na=True, unit="s"),
            "answer_rate": Knob(float, "Signed answers each controller regains a second "
                                "(imbu's answer budget).", unit="/s"),
            "answer_burst": Knob(int, "Signed answers each controller may hold unspent."),
            "operation_timeout_s": Knob(float, "How long an operation may run before the "
                                        "adapter gives up on it.", unit="s"),
            "homes": Knob(int, "How many imbu issuers, each its own home and process, share "
                          "the deployment (this.i 2lkqz9mq)."),
            "clients": Knob(int, "How many controllers each home serves, each with its own key "
                            "and connection pool."),
            "signer": Knob(str, "lockbox: the principal's keys are in the lockbox; external: "
                           "heti's ThrottledSigner holds them (this.i 2p26rn9h)."),
            "signer_latency_ms": Knob(float, "The external signer's latency per operation.",
                                      allow_na=True, unit="ms"),
            "signer_rate": Knob(float, "The external signer's cap on operation starts per "
                                "second.", allow_na=True, unit="/s"),
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
        check_signer(self.ctx.knobs)
        check_waiting(self.ctx.knobs)
        check_budget(self.ctx.knobs)
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
            home.signer_port = free_port()
            args += signer_args(self.ctx.knobs, port=home.signer_port)
            args += budget_args(self.ctx.knobs)
            process = await self.ctx.procs.start(
                f"issuer.h{index}", (PYTHON, "-m", "keri_bench.stacks.issuer"), tuple(args),
                env={"BENCH_PASSCODE": passcode(),
                     "IMBU_HOME": str(self.ctx.run_dir / "imbu" / f"h{index}"),
                     "BENCH_PUBLISHER_SEED": state["publisher_seed"],
                     # One salt per home for the run, so a restart attaches the same keys.
                     "BENCH_SIGNER_SALT": Salter().qb64}, timeout=STARTUP)
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
        """Bytes under every home's directory: imbu's operation store and heti's lockbox. The
        Registrar's and Observer's stores are not counted (~4hqv)."""
        total = 0
        for root, _, files in os.walk(Path(self.ctx.run_dir) / "imbu"):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except FileNotFoundError:
                    pass  # removed between the listing and the stat, as SQLite's journals are
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

    async def _sign(self, client: Client, method: str, url: str,
                    body: bytes | None) -> dict[str, str]:
        """The headers a controller signs a request with: its own key, here."""
        return fiki.sign_request(key=client.key, method=method, url=url, body=body)

    def _wait(self) -> int | None:
        """Seconds to prefer imbu holds an answer, or None when the adapter polls. The bulk
        adapter's knobs name no wait, so it polls."""
        wait = self.ctx.knobs.get("wait_s", NA)
        return None if wait == NA else int(wait)

    async def _call(self, home: Home, client: Client, method: str, path: str,
                    payload: Mapping | None = None, key: str | None = None,
                    deadline: int | None = None) -> Answer:
        """One request, signed afresh each time it is sent, and sent again after a refusal's
        Retry-After until ``deadline`` (by default the operation timeout from now)."""
        url = f"{home.control}{path}"
        body = json.dumps(payload).encode() if payload is not None else None
        if deadline is None:
            deadline = self.ctx.now() + round(self.ctx.knobs["operation_timeout_s"] * 1e9)
        while True:
            headers = {**await self._sign(client, method, url, body),
                       "Content-Type": "application/json"}
            if key is not None:
                headers["Idempotency-Key"] = key
            if (wait := self._wait()) is not None:
                headers["Prefer"] = f"wait={wait}"
            answer = await client.http.request(method, url, body=body, headers=headers)
            if any(name.lower() == "signature" for name in answer.headers):
                home.signed += 1
            pause = retry_after(answer) if answer.status == 429 else None
            if pause is None:
                return answer
            home.refused += 1
            if self.ctx.now() + pause * 1_000_000_000 > deadline:
                raise BenchError(E_TIMEOUT, f"imbu refused {method} {path} until {pause} s "
                                 f"from now, past the operation's deadline.")
            await sleep(pause)

    async def census(self) -> dict | None:
        """Each home's external signer's record, with the answers imbu signed and the
        issuances it accepted (this.i 2p26rn9h); None when the lockbox signs."""
        if self.ctx.knobs["signer"] != "external":
            return None
        signers = []
        wanted = {name: None if self.ctx.knobs[f"signer_{name}"] == NA
                  else self.ctx.knobs[f"signer_{name}"] for name in ("latency_ms", "rate")}
        for home in self.homes:
            answer = await home.clients[0].http.get(
                f"http://127.0.0.1:{home.signer_port}/signer")
            record = answer.json() if answer.status == 200 else None
            # The record must be this run's signer, at the profile the scenario set.
            if not isinstance(record, dict) or \
                    {name: record.get(name) for name in wanted} != wanted:
                raise BenchError(E_SIGNER_RECORD, f"Home {home.index}'s signer answered "
                                 f"{answer.status} with a record that is not the profile "
                                 f"{wanted}: {answer.body[:200]!r}")
            signers.append({**record, "signed_responses": home.signed,
                            "refused_answers": home.refused, "issuances": home.issuances})
        return {"signers": signers}

    async def _finish(self, home: Home, client: Client, operation: str,
                      first: dict | None = None) -> dict:
        """Read the operation until it is done. ``first`` is the 202's own document, which a
        waiting client takes as the answer when it is already settled; a polling client passes
        none and always reads, as the baselines' client did, so their counts compare."""
        knobs = self.ctx.knobs
        deadline = self.ctx.now() + round(knobs["operation_timeout_s"] * 1e9)
        waiting = self._wait() is not None
        op = first
        while True:
            if op is not None:
                if op.get("state") == "done":
                    return op
                if op.get("state") == "failed":
                    raise BenchError(E_FAILED, f"imbu's operation {operation} failed: "
                                     f"{op.get('error')!r}"[:500])
                if self.ctx.now() >= deadline:
                    raise BenchError(E_TIMEOUT, f"imbu's operation {operation} had not "
                                     f"finished after {knobs['operation_timeout_s']} s.")
                if not waiting:
                    await asyncio.sleep(knobs["poll_ms"] / 1e3)
            answer = await self._call(home, client, "GET", f"/v1/imbu/operations/{operation}",
                                      deadline=deadline)
            if answer.status != 200:
                raise BenchError(E_REFUSED, f"imbu answered {answer.status} when asked for "
                                 f"operation {operation}: {answer.body[:300]!r}")
            op = answer.json()

    async def _settled(self, home: Home, client: Client, answer: Answer) -> dict:
        """The finished operation a 202 began."""
        first = operation_document(answer) if self._wait() is not None else None
        return await self._finish(home, client, operation_id(answer), first)

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
            resolved = (await self._settled(home, client, answer)).get("result")
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
        home.issuances += 1
        op = await self._settled(home, client, answer)
        result = op.get("result") or {}
        said = result.get("said")
        self._report(op, credential, {name: name for name in ("accepted", *ISSUE_MILESTONES)},
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
        self._report(await self._settled(home, client, answer), credential,
                     REVOKE_MILESTONES, self._where(home, client))

    async def stop(self) -> None:
        for home in self.homes:
            for client in home.clients:
                await client.http.close()
