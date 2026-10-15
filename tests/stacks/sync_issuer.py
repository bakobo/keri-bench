"""A test-only issuer for imbu's synchronous routes, so the downstream roles can be exercised with
real credentials before imbu-async lands. It declares no milestone, because a synchronous answer
cannot say when anchoring or witnessing happened; it is never registered as a product adapter."""

import base64

from keri_bench.adapters.base import Declaration, Issued
from keri_bench.errors import BenchError
from keri_bench.stacks.imbu import ImbuIssuer
from keri_bench.vocabulary import Role


class SyncImbuIssuer(ImbuIssuer):
    declaration = Declaration(adapter="test.sync-imbu.issuer", role=Role.ISSUER, version="test",
                              milestones={}, knobs=ImbuIssuer.declaration.knobs,
                              tested_against={"imbu": "6c0cfc7"})

    async def issue(self, credential, holder):
        answer = await self._call("POST", "/v1/imbu/issuances",
                                  {"recipient": holder["aid"],
                                   "attributes": {"benchCredential": credential}})
        if answer.status != 200:
            raise BenchError("e.party.imbu.refused.f", f"{answer.status} {answer.body[:300]!r}")
        found = answer.json()
        self.ctx.shared.setdefault("said", {})[credential] = found["said"]
        return Issued(credential, found["said"], base64.b64decode(found["grant"]), False)

    async def revoke(self, credential):
        answer = await self._call("POST", "/v1/imbu/revocations",
                                  {"said": self.ctx.shared["said"][credential]})
        if answer.status != 200:
            raise BenchError("e.party.imbu.refused.f", f"{answer.status} {answer.body[:300]!r}")
