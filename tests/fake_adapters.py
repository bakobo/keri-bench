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


from keri_bench.adapters.base import WitnessPoolAdapter  # noqa: E402


class FakeWitnessPool(WitnessPoolAdapter):
    """Fronts one address itself, as a stack whose processes advertise their own URL must."""

    declaration = Declaration(
        adapter="fake.witness_pool", role=Role.WITNESS_POOL, version="1", milestones={},
        knobs=KnobSchema(1, {}), tested_against={"fake": "1"})

    async def start(self):
        fronted = await self.ctx.front("advertised", "http://127.0.0.1:9")
        return {"oobis": [f"{fronted}/oobi/B1/controller", "http://127.0.0.1:8/oobi/B2"],
                "aid": "Bwitness", "single": "http://127.0.0.1:7/x"}


class FakeRevokingIssuer(FakeIssuer):
    async def revoke(self, credential):
        return None


class ScriptedVerifier(VerifierAdapter):
    """Accepts or refuses every presentation, and answers status polls from a script whose
    last answer repeats: ``answers`` is a string of r (revoked) and v (valid)."""

    declaration = Declaration(
        adapter="fake.scripted_verifier", role=Role.VERIFIER, version="1", milestones={},
        knobs=KnobSchema(1, {"accept": Knob(bool, "Accept presentations."),
                             "answers": Knob(str, "Status answers, r or v, the last repeating.")}),
        tested_against={"fake": "1"})

    async def identity(self):
        return {"aid": "Vscripted"}

    async def verify(self, credential, presentation):
        return self.ctx.knobs["accept"]

    async def status(self, credential):
        polls = self.ctx.shared.setdefault("polls", {})
        index = polls.get(credential, 0)
        polls[credential] = index + 1
        script = self.ctx.knobs["answers"]
        answer = script[min(index, len(script) - 1)]
        return {"status": "revoked" if answer == "r" else "valid", "said": None}
