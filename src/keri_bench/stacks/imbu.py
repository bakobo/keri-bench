"""The imbu issuer adapter, built against imbu's asynchronous operations (plan item imbu-async).

imbu's control routes are becoming asynchronous: a mutating request answers 202 with an
operation, and the operation resource records each lifecycle milestone with the time it happened
(the imbu-async brief, ~/code/bakobo/utah-keri-adoption/plan/briefs/imbu-async.md, decision
Q-EVAG). This adapter reads those times as ``reported`` events. It has no synchronous fallback, on
purpose: a synchronous issuance answers once witnessing is done, so anchored and witnessed could
only be inferred, and an inferred milestone is never reported (this.i 7wt73dlu).

What the brief does not pin down is read defensively and named here, so it is easy to correct
once imbu-async lands:
- the operation's id comes from the 202 body's ``operation`` (or ``id``), or its Location header;
- milestones are ``op["milestones"][name]``, integer nanoseconds or ISO 8601, on imbu's wall
  clock, which on the same host is the harness's ``realtime`` domain with a measured offset;
- the operation is finished when ``state`` is ``done``, and failed when it is ``failed``, with
  ``result.said`` and ``result.grant`` (base64) on success;
- the client's idempotency key travels as the ``Idempotency-Key`` header.
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import json
from collections.abc import Mapping
from typing import Any

import fiki  # the stacks extra; registry.lookup names it when it is missing

from ..adapters.base import Declaration, IssuerAdapter, Issued, Observation
from ..errors import BenchError
from ..http import Answer, HttpClient
from ..knobs import Knob, KnobSchema
from ..procs import free_port
from ..vocabulary import Role
from .adapters import IMBU, PYTHON, STARTUP, passcode

VERSION = "1"
WALL = 1_000
"""imbu's milestone stamps are taken as wall-clock nanoseconds; a microsecond is the resolution
claimed for them until imbu-async says otherwise."""

ISSUE_MILESTONES = ("constructed", "registry_updated", "anchored", "witnessed", "published")
REVOKE_MILESTONES = {"anchored": "revocation_anchored", "witnessed": "revocation_witnessed",
                     "published": "revocation_published"}

E_REFUSED = "e.party.imbu.refused.f"
E_FAILED = "e.party.imbu.operation.f"
E_SHAPE = "e.input.format.imbu.operation.f"
E_TIMEOUT = "e.env.imbu.operation.r"


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
        knobs=KnobSchema(1, {
            "toad": Knob(int, "Witness receipts imbu requires before an event is settled."),
            "poll_ms": Knob(float, "How often the adapter reads an operation until it finishes; "
                            "the milestone times come from imbu, not from this poll.",
                            unit="ms"),
            "operation_timeout_s": Knob(float, "How long an operation may run before the "
                                        "adapter gives up on it.", unit="s"),
        }),
        tested_against={"imbu": "imbu-async, not yet landed"})

    OPERATOR_ACTS = "create {record, commitment};destroy commitment"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.client = HttpClient()
        self.key = None
        self.control = ""

    async def start(self):
        """imbu in its own process with the Registrar publisher wired (stacks/issuer.py)."""
        self.key = fiki.Key.generate()
        state = self.ctx.peers[Role.STATE_DISTRIBUTION]
        args = ["--toad", str(self.ctx.knobs["toad"]), "--registrar", state["registrar"],
                "--controller", f"{self.key.aid}={self.OPERATOR_ACTS}",
                "--control-port", str(free_port())]
        for oobi in self.ctx.peers[Role.WITNESS_POOL]["oobis"]:
            args += ["--witness", oobi]
        process = await self.ctx.procs.start(
            "issuer", (PYTHON, "-m", "keri_bench.stacks.issuer"), tuple(args),
            env={"BENCH_PASSCODE": passcode(), "IMBU_HOME": str(self.ctx.run_dir / "imbu"),
                 "BENCH_PUBLISHER_SEED": state["publisher_seed"]}, timeout=STARTUP)
        found = process.announced
        return {"control": found["control"], "oobis": found["oobis"],
                "registry": found["registry"], "issuer": found["issuer"]}

    async def connect(self):
        self.control = self.ctx.peers[Role.ISSUER]["control"]
        return {}

    async def versions(self):
        return {"imbu": IMBU}

    def _sign(self, method: str, url: str, body: bytes | None) -> dict[str, str]:
        return fiki.sign_request(key=self.key, method=method, url=url, body=body)

    async def _call(self, method: str, path: str, payload: Mapping | None = None,
                    key: str | None = None) -> Answer:
        url = f"{self.control}{path}"
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {**self._sign(method, url, body), "Content-Type": "application/json"}
        if key is not None:
            headers["Idempotency-Key"] = key
        return await self.client.request(method, url, body=body, headers=headers)

    async def _finish(self, operation: str) -> dict:
        knobs = self.ctx.knobs
        deadline = self.ctx.now() + round(knobs["operation_timeout_s"] * 1e9)
        while True:
            answer = await self._call("GET", f"/v1/imbu/operations/{operation}")
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
                said: str | None) -> None:
        milestones = op.get("milestones") or {}
        for theirs, ours in names.items():
            if theirs in milestones:
                self.ctx.emit(ours, credentials=[credential], t=stamp(milestones[theirs]),
                              attrs={"said": said} if said else None)

    async def issue(self, credential: str, holder: Mapping[str, Any]) -> Issued:
        answer = await self._call("POST", "/v1/imbu/issuances",
                                  {"recipient": holder["aid"],
                                   "attributes": {"benchCredential": credential}},
                                  key=credential)
        if answer.status != 202:
            raise BenchError(E_REFUSED, f"imbu answered {answer.status} to an issuance, not "
                             f"202: {answer.body[:300]!r}")
        self.ctx.emit("accepted", credentials=[credential])
        op = await self._finish(operation_id(answer))
        result = op.get("result") or {}
        said = result.get("said")
        self._report(op, credential, {name: name for name in ISSUE_MILESTONES}, said)
        self.ctx.shared.setdefault("said", {})[credential] = said
        grant = base64.b64decode(result["grant"]) if result.get("grant") else None
        return Issued(credential, said, grant, delivered_by_stack=False)

    async def revoke(self, credential: str) -> None:
        self.ctx.emit("revocation_requested", credentials=[credential])
        answer = await self._call("POST", "/v1/imbu/revocations",
                                  {"said": self.ctx.shared["said"][credential]},
                                  key=f"revoke-{credential}")
        if answer.status != 202:
            raise BenchError(E_REFUSED, f"imbu answered {answer.status} to a revocation, not "
                             f"202: {answer.body[:300]!r}")
        self._report(await self._finish(operation_id(answer)), credential, REVOKE_MILESTONES,
                     None)

    async def stop(self) -> None:
        await self.client.close()
