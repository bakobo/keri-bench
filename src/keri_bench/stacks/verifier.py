"""An independent heti verifier in a process of its own: ``python -m keri_bench.stacks.verifier``.

It is not the issuer's code path (this.i 27mjdj7q): its own lockbox admits the holder's
presentation, then checks the credential is current against the Observer, which is the only
place it takes TEL state from. Routes, each JSON:

- ``POST /verify/<credential>`` with ``{"grant": base64, "oobi": holder OOBI}``: 200 with
  ``{"valid": true, "said", "registry"}``, or 409 with the refusal's code;
- ``POST /status/<said>`` with a fresh presentation, ``{"grant", "oobi"}``: ``{"status":
  "valid"|"revoked"|"unknown"}``, judged against the Observer as of now with the disclosures
  that presentation carries, for a credential this verifier has verified before. A blinded
  registry's later state, such as a revocation, reads only with newer disclosures than the first
  presentation's, so a fresh presentation is how this verifier recognises a revocation (heti
  this.i, the AdmitResult.blinders node under @0ky6uwqz; the Observer-side blinder feed that
  would make the holder unnecessary is the unlanded half of heti tick 7vhf);
- ``POST /sync/<name>``: reads and parses the chain of every registry this verifier has met, as a
  cold verifier's first synchronization does, and answers how many registries and events.
"""

from __future__ import annotations

import asyncio
import base64
import json

from ..synthetic import http
from . import announce, party, until_terminated

REVOKED = "e.state.conflict.acdc.revoked."


def count_events(chain: bytes) -> int:
    """How many events a registry chain holds, read as a cold verifier reads them."""
    from keri.core import Counter, serdering
    from keri.kering import Version
    genus = bytes(Counter.makeGVC(version=Version))
    body = chain[len(genus):] if chain.startswith(genus) else chain
    count = 0
    while body:
        body = body[serdering.SerderACDC(raw=body).size:]
        count += 1
    return count


class Verifier:
    def __init__(self, box, facet, source, freshness: float) -> None:
        self.box, self.facet, self.source, self.freshness = box, facet, source, freshness
        self.met: set[str] = set()
        self.known: dict[str, str] = {}
        """Each credential this verifier has accepted, by SAID, and its registry."""

    async def handle(self, method: str, path: str, body: bytes):
        path = path.partition("?")[0]
        route, _, name = path.strip("/").partition("/")
        if method == "GET" and route == "identity":
            return 200, {"aid": self.facet.aid, "oobi": self.facet.oobis[0]}
        if method == "POST" and route == "verify":
            return await self.verify(json.loads(body))
        if method == "POST" and route == "status":
            return 200, {"status": await self.status(name, json.loads(body))}
        if method == "POST" and route == "sync":
            registries = sorted(set(self.known.values()))
            events = 0
            for registry in registries:
                events += count_events((await self.source.read(registry)).chain)
            return 200, {"registries": len(registries), "events": events}
        return 404, {"error": f"There is no route {method} {path}."}

    async def verify(self, request: dict):
        try:
            if request["oobi"] not in self.met:
                await self.box.add_other(oobi=request["oobi"])
                self.met.add(request["oobi"])
            grant = base64.b64decode(request["grant"], validate=True)
            admitted = await self.facet.admit(grant=grant, recency=party.RECENCY)
            # A verifier is not retained as the credential's holder, so the disclosures admit
            # verified with are the only copy it has (heti AdmitResult.blinders, @9zc89rwz).
            verdict = await self.box.verify_current_acdc(
                acdc=admitted.acdc, source=self.source, freshness=self.freshness,
                recency=party.RECENCY, blinders=admitted.blinders)
        except Exception as refused:
            return 409, {"valid": False, "code": party.code(refused),
                         "error": str(refused)[:500]}
        self.known[verdict.said] = verdict.registry
        return 200, {"valid": verdict.state == "issued", "said": verdict.said,
                     "registry": verdict.registry}

    async def status(self, said: str, request: dict) -> str:
        """What this verifier says of the credential now, from a fresh presentation of it."""
        if said not in self.known:
            return "unknown"
        try:
            grant = base64.b64decode(request["grant"], validate=True)
            admitted = await self.facet.admit(grant=grant, recency=party.RECENCY)
            if admitted.verdict.said != said:
                return "unknown"
            verdict = await self.box.verify_current_acdc(
                acdc=admitted.acdc, source=self.source, freshness=self.freshness,
                recency=party.RECENCY, blinders=admitted.blinders)
        except Exception as refused:
            return "revoked" if party.code(refused).startswith(REVOKED) else "unknown"
        return "valid" if verdict.state == "issued" else "unknown"


async def serve(args) -> None:
    from heti.anchored.produce.observed import RegistrySource
    box, facet = await party.open_party(args)
    verifier = Verifier(box, facet, RegistrySource(args.observer_url, aid=args.observer_aid),
                        args.freshness)
    server = await http.serve(verifier.handle, args.port)
    announce(url=f"http://127.0.0.1:{args.port}", aid=facet.aid, oobi=facet.oobis[0])
    async with server:
        await until_terminated()


def main(argv=None) -> None:
    parser = party.arguments(__doc__)
    parser.add_argument("--observer-url", required=True)
    parser.add_argument("--observer-aid", required=True)
    parser.add_argument("--freshness", type=float, required=True)
    asyncio.run(serve(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
