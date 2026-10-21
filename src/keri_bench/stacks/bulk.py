"""The imbu bulk issuer adapter: SEDI's proofed bulk ceremony (this.i 4f987y88).

A request is one ceremony. The citizens process (stacks/citizens.py) has its proofing agent sign
an IAR for one citizen and the citizen anchor the IAR's nonce; the adapter then posts the
ceremony to imbu's ``/v1/imbu/bulk-issuances`` with the IAR's SAID as the idempotency key, every
request and poll signed by that citizen's SMAID through the citizens process, and reports imbu's
own stamps as ``ImbuIssuer`` does. imbu issues the management Core and Residence pair and
``copies`` copy pairs, each copy to a fresh key's AID, every credential in a registry of its own
reserved from a pool the operator provisions, at the size the scenario sets, before the window
opens. The ceremony's events name the ceremony; each carries how many credentials it made.

Citizens are this adapter's clients: ``clients`` SMAIDs, each a configured controller of every
home, and ceremonies are dealt to homes and then citizens as ``ImbuIssuer`` deals credentials.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
from collections.abc import Mapping
from typing import Any

import fiki

from ..adapters.base import Declaration, Issued, Observation
from ..errors import BenchError
from ..http import HttpClient
from ..knobs import Knob, KnobSchema
from ..procs import free_port
from ..vocabulary import Role
from . import sedi
from .adapters import IMBU, PYTHON, STARTUP, passcode
from .imbu import (E_DEPLOYMENT, E_REFUSED, E_SHAPE, ISSUE_MILESTONES, VERSION, WALL, Client, Home,
                   ImbuIssuer, operation_id, stamp)

E_UNSUPPORTED = "e.feature.unsupported.imbu.bulk.revoke.f"
POOL_BATCH = 1000
"""imbu provisions at most this many registries per registry-pool request."""


SAID = re.compile(r"[A-Za-z0-9_-]{44}")
"""A SAID in CESR text, as imbu names a registry or a credential."""


def registries_made(op: dict, *, count: int) -> int:
    """How many distinct registries a finished pool provisioning names, refused unless it is
    the count asked for: done is not evidence of what was done."""
    result = op.get("result")
    made = result.get("registries") if isinstance(result, dict) else None
    if not isinstance(made, list) or len(made) != count or len(set(made)) != count or \
            not all(isinstance(r, str) and SAID.fullmatch(r) for r in made):
        raise BenchError(E_SHAPE, f"imbu's finished pool provisioning does not name {count} "
                         f"distinct registries: {result!r}"[:500])
    return count


def resolved(op: dict, *, aid: str) -> None:
    result = op.get("result")
    if not isinstance(result, dict) or result.get("aid") != aid:
        raise BenchError(E_SHAPE, f"imbu resolved a party of the ceremony to {result!r}, not "
                         f"{aid!r}."[:500])


def copies_issued(op: dict, *, copies: int, seen: set) -> str:
    """The first copy's Core SAID, once the ceremony's result names a Core and a Residence SAID
    for every copy asked for; stamps alone are not evidence the credentials exist."""
    result = op.get("result")
    made = result.get("copies") if isinstance(result, dict) else None
    if not isinstance(made, list) or len(made) != copies or not all(
            isinstance(pair, dict) and all(isinstance(pair.get(kind), dict)
                                           and isinstance(pair[kind].get("said"), str)
                                           and pair[kind]["said"]
                                           for kind in ("core", "residence"))
            for pair in made) or not all(SAID.fullmatch(pair[kind]["said"]) for pair in made
                                         for kind in ("core", "residence")):
        raise BenchError(E_SHAPE, f"imbu's finished ceremony does not name a Core and a "
                         f"Residence for each of its {copies} copies: {result!r}"[:500])
    saids = [pair[kind]["said"] for pair in made for kind in ("core", "residence")]
    if len(set(saids)) != len(saids) or seen.intersection(saids):
        raise BenchError(E_SHAPE, f"imbu's finished ceremony names a credential twice or one "
                         f"an earlier ceremony issued: {saids!r}"[:500])
    seen.update(saids)
    # The management pair imbu retains is not in the result; counting it rests on imbu (~7soc).
    return made[0]["core"]["said"]


class ImbuBulkIssuer(ImbuIssuer):
    declaration = Declaration(
        adapter="imbu.bulk_issuer", role=Role.ISSUER, version=VERSION,
        milestones={"accepted": Observation("observed", "harness"),
                    **{name: Observation("reported", "realtime", WALL)
                       for name in ISSUE_MILESTONES}},
        knobs=KnobSchema(1, {
            "toad": Knob(int, "Witness receipts imbu and the citizens require."),
            "poll_ms": Knob(float, "How often the adapter reads a ceremony's operation.",
                            unit="ms"),
            "operation_timeout_s": Knob(float, "How long a ceremony may run before the adapter "
                                        "gives up on it.", unit="s"),
            "homes": Knob(int, "How many imbu issuers share the deployment."),
            "clients": Knob(int, "How many citizens, each a SMAID every home trusts."),
            "copies": Knob(int, "Copy pairs per ceremony, beside the management pair."),
            "pool": Knob(int, "Registries each home provisions before the window: at least "
                         "2 (copies + 1) per ceremony it will see."),
        }),
        tested_against={"imbu": IMBU})

    OPERATOR_ACTS = ("create authority;create record;destroy commitment;"
                     "create relationship;observe info")
    CITIZEN_ACTS = "create {record, commitment}"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.citizens = HttpClient()
        self.citizens_url = ""
        self.token = ""
        self.issued: set[str] = set()
        """Every credential SAID a ceremony has named, so none is counted twice."""
        self.pool: list[dict] = []
        """Each registry-pool provisioning, with imbu's own accepted and finished stamps."""

    async def start(self):
        knobs = self.ctx.knobs
        homes, citizens, copies = knobs["homes"], knobs["clients"], knobs["copies"]
        if homes < 1 or citizens < 1 or not 1 <= copies <= 50 or knobs["pool"] < 0:
            raise BenchError(E_DEPLOYMENT, f"The imbu bulk issuer needs at least one home and "
                             f"one citizen, 1 to 50 copies (imbu's limit) and a pool that is not "
                             f"negative, not {homes}, {citizens}, {copies} and {knobs['pool']}.")
        witnesses = self.ctx.peers[Role.WITNESS_POOL]["oobis"]
        args = ["--toad", str(knobs["toad"]), "--citizens", str(citizens), "--port",
                str(free_port()), "--store", str(self.ctx.run_dir / "citizens")]
        for oobi in witnesses:
            args += ["--witness", oobi]
        self.token = secrets.token_urlsafe(24)
        process = await self.ctx.procs.start(
            "citizens", (PYTHON, "-m", "keri_bench.stacks.citizens"), tuple(args),
            env={"BENCH_PASSCODE": passcode(), "BENCH_CITIZENS_TOKEN": self.token},
            timeout=STARTUP)
        self.citizens_url = process.announced["url"]
        self.cast = (await self.citizens.get(f"{self.citizens_url}/identity")).json()
        state = self.ctx.peers[Role.STATE_DISTRIBUTION]
        found = []
        for index in range(homes):
            home = Home(index, clients=[Client(None, citizen=k) for k in range(citizens)],
                        operator=Client(fiki.Key.generate()))
            args = ["--toad", str(knobs["toad"]), "--registrar-url", state["registrar"],
                    "--control-port", str(free_port()), "--bulk", "--proofer",
                    self.cast["proofer"]["aid"], "--controller",
                    f"{home.operator.key.aid}={self.OPERATOR_ACTS}"]
            for citizen in self.cast["citizens"]:
                args += ["--controller", f"{citizen['aid']}={self.CITIZEN_ACTS}"]
            for oobi in witnesses:
                args += ["--witness", oobi]
            process = await self.ctx.procs.start(
                f"issuer.h{index}", (PYTHON, "-m", "keri_bench.stacks.issuer"), tuple(args),
                env={"BENCH_PASSCODE": passcode(),
                     "IMBU_HOME": str(self.ctx.run_dir / "imbu" / f"h{index}"),
                     "BENCH_PUBLISHER_SEED": state["publisher_seed"]}, timeout=STARTUP)
            home.agent = process.announced["agent"]
            self.homes.append(home)
            found.append(process.announced)
        return {"control": [f["control"] for f in found],
                "oobis": [f["oobis"][0] for f in found],
                "issuer": [f["issuer"] for f in found]}

    async def _sign(self, client: Client, method: str, url: str,
                    body: bytes | None) -> dict[str, str]:
        """A citizen's request is signed by its SMAID, in the citizens process."""
        if client.citizen is None:
            return await super()._sign(client, method, url, body)
        answer = await self.citizens.post(
            f"{self.citizens_url}/sign/{client.citizen}",
            body=json.dumps({"token": self.token, "method": method, "url": url,
                             "body": None if body is None
                             else base64.b64encode(body).decode()}).encode())
        return answer.json()["headers"]

    async def _settle(self, home: Home, answer, what: str) -> dict:
        if answer.status != 202:
            raise BenchError(E_REFUSED, f"imbu answered {answer.status} to {what}, not 202: "
                             f"{answer.body[:300]!r}")
        return await self._finish(home, home.operator, operation_id(answer))

    async def meet(self, holder: Mapping[str, Any]) -> None:
        """Before the window, every home resolves the proofing agent and every citizen, and
        provisions its pool of registries: registry inception, apart from issuance."""
        cast = [self.cast["proofer"], *self.cast["citizens"]]
        for home in self.homes:
            for party in cast:
                resolved(await self._settle(home, await self._call(
                    home, home.operator, "POST", "/v1/imbu/resolutions",
                    {"oobi": party["oobi"]}, key=f"meet-{party['aid']}"),
                    "resolving a party of the ceremony"), aid=party["aid"])
            made = 0
            while made < self.ctx.knobs["pool"]:
                count = min(POOL_BATCH, self.ctx.knobs["pool"] - made)
                op = await self._settle(home, await self._call(
                    home, home.operator, "POST", "/v1/imbu/registry-pool", {"count": count},
                    key=f"pool-{made}"), "provisioning registries")
                stamps = op.get("milestones") or {}
                self.pool.append({"home": home.index,
                                  "registries": registries_made(op, count=count),
                                  **{f"{name}_ns": stamp(stamps[name])
                                     for name in ("accepted", "finished") if name in stamps}})
                made += count

    async def issue(self, credential: str, holder: Mapping[str, Any]) -> Issued:
        home, client = self._deal(credential)
        where = self._where(home, client)
        copies = self.ctx.knobs["copies"]
        made = (await self.citizens.post(f"{self.citizens_url}/ceremony/{client.citizen}",
                                         body=json.dumps({"token": self.token}).encode())).json()
        document = sedi.ceremony(copies=[fiki.Key.generate().aid for _ in range(copies)],
                                 agent=home.agent, iar=base64.b64decode(made["iar"]))
        answer = await self._call(home, client, "POST", "/v1/imbu/bulk-issuances", document,
                                  key=made["said"])
        if answer.status != 202:
            raise BenchError(E_REFUSED, f"imbu answered {answer.status} to a bulk ceremony, "
                             f"not 202: {answer.body[:300]!r}")
        home.issuances += 1
        self.ctx.emit("accepted", credentials=[credential], attrs=where)
        op = await self._finish(home, client, operation_id(answer))
        said = copies_issued(op, copies=copies, seen=self.issued)
        self._report(op, credential, {name: name for name in ISSUE_MILESTONES},
                     {**where, "credentials": 2 * (copies + 1)})
        return Issued(credential, said, None, delivered_by_stack=False)

    async def revoke(self, credential: str) -> None:
        raise BenchError(E_UNSUPPORTED, "The bulk issuer does not revoke: bulk-steady measures "
                         "issuance, and a ceremony's credentials have no one SAID to revoke.")

    async def census(self) -> dict:
        """No signer is counted here, but registry inception is: each pool provisioning,
        stamped by imbu on its realtime clock, so its rate is apart from issuance's."""
        return {"signers": [], "pool": self.pool}

    async def stop(self) -> None:
        await self.citizens.close()
        for home in self.homes:
            await home.operator.http.close()
        await super().stop()
