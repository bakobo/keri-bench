"""In-process fake adapters with no stack behind them, for the run machinery's edge cases and
for the adapter contract suite's own tests."""

import asyncio

from keri_bench.adapters.base import (Declaration, HolderAdapter, IssuerAdapter, Issued,
                                      Observation, VerifierAdapter)
from keri_bench.knobs import Knob, KnobSchema
from keri_bench.vocabulary import Role

KNOBS = KnobSchema(1, {"issue_s": Knob(float, "How long issuance takes.", unit="s")})


class FakeIssuer(IssuerAdapter):
    declaration = Declaration(
        adapter="fake.issuer", role=Role.ISSUER, version="1",
        milestones={"constructed": Observation("observed", "harness"),
                    "grant_sent": Observation("observed", "harness")},
        knobs=KNOBS, tested_against={"fake": "1"})

    async def start(self):
        return {}

    async def issue(self, credential, holder):
        await asyncio.sleep(self.ctx.knobs["issue_s"])
        self.ctx.emit("constructed", credentials=[credential])
        self.ctx.emit("grant_sent", credentials=[credential])
        return Issued(credential, f"E{credential}", None, delivered_by_stack=True)

    async def versions(self):
        return {"fake": "1"}


class FakeHolder(HolderAdapter):
    declaration = Declaration(
        adapter="fake.holder", role=Role.HOLDER, version="1",
        milestones={"stored": Observation("observed", "harness")},
        knobs=KnobSchema(1, {}), tested_against={"fake": "1"})

    async def identity(self):
        return {"aid": "Hfake"}

    async def receive(self, issued):
        self.ctx.emit("stored", credentials=[issued.credential])


class FakeVerifier(VerifierAdapter):
    """Answers status by raising, as a verifier that has gone away would."""

    declaration = Declaration(
        adapter="fake.verifier", role=Role.VERIFIER, version="1", milestones={},
        knobs=KnobSchema(1, {}), tested_against={"fake": "1"})

    async def identity(self):
        return {"aid": "Vfake"}

    async def status(self, credential):
        raise ConnectionError("the verifier went away")
