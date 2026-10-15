"""An independent heti verifier in a process of its own: ``python -m keri_bench.stacks.verifier``.

It is not the issuer's code path (this.i 27mjdj7q): its own lockbox admits the holder's
presentation, then checks the credential is current against the Observer, which is the only
place it takes TEL state from. Routes, each JSON:

- ``POST /verify/<credential>`` with ``{"grant": base64, "oobi": holder OOBI}``: 200 with
  ``{"valid": true, "said", "registry"}``, or 409 with the refusal's code;
- ``GET /status/<said>``: ``{"status": "valid"|"revoked"|"unknown"}``, judged against the
  Observer as of now, for a credential this verifier has verified before;
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


def states(chain: bytes) -> tuple[dict[str, str], int]:
    """Each credential SAID's latest state in a registry chain, and how many events it holds.

    imbu's simple issuance shares one registry whose ``upd`` events name a credential (``td``)
    and its state (``ts``); the latest event naming a SAID decides it. This mirrors heti's
    private ``verifying._chain`` parse, which has no public form.
    """
    from keri.core import Counter, serdering
    from keri.kering import Ilks, Version
    genus = bytes(Counter.makeGVC(version=Version))
    body = chain[len(genus):] if chain.startswith(genus) else chain
    found, count = {}, 0
    while body:
        serder = serdering.SerderACDC(raw=body)
        body = body[serder.size:]
        count += 1
        if serder.ilk == Ilks.upd and serder.sad.get("td"):
            found[serder.sad["td"]] = serder.sad.get("ts")
    return found, count


def disclosures(grant: bytes) -> list[str] | None:
    """The blinded-registry disclosures a holder's grant carries, signed in its ``a.blinders``.

    heti's admit reads them but does not hand them back, and ``verify_current_acdc`` needs them
    to read a blinded registry's state from the Observer: without them the state names no
    credential, and the check fails as a binding mismatch.
    """
    from keri.core import Counter, serdering
    from keri.kering import Version
    genus = bytes(Counter.makeGVC(version=Version))
    body = grant[len(genus):] if grant.startswith(genus) else grant
    return serdering.SerderKERI(raw=bytearray(body)).sad.get("a", {}).get("blinders")


class Verifier:
    def __init__(self, box, facet, source, freshness: float) -> None:
        self.box, self.facet, self.source, self.freshness = box, facet, source, freshness
        self.met: set[str] = set()
        self.known: dict[str, tuple[bytes, list | None, str]] = {}

    async def handle(self, method: str, path: str, body: bytes):
        path = path.partition("?")[0]
        route, _, name = path.strip("/").partition("/")
        if method == "GET" and route == "identity":
            return 200, {"aid": self.facet.aid, "oobi": self.facet.oobis[0]}
        if method == "POST" and route == "verify":
            return await self.verify(json.loads(body))
        if method == "GET" and route == "status":
            return 200, {"status": await self.status(name)}
        if method == "POST" and route == "sync":
            registries = sorted({registry for _, _, registry in self.known.values()})
            events = 0
            for registry in registries:
                events += states((await self.source.read(registry)).chain)[1]
            return 200, {"registries": len(registries), "events": events}
        return 404, {"error": f"There is no route {method} {path}."}

    async def verify(self, request: dict):
        try:
            if request["oobi"] not in self.met:
                await self.box.add_other(oobi=request["oobi"])
                self.met.add(request["oobi"])
            grant = base64.b64decode(request["grant"], validate=True)
            admitted = await self.facet.admit(grant=grant, recency=party.RECENCY)
            verdict = await self.box.verify_current_acdc(
                acdc=admitted.acdc, source=self.source, freshness=self.freshness,
                recency=party.RECENCY, blinders=disclosures(grant))
        except Exception as refused:
            return 409, {"valid": False, "code": party.code(refused),
                         "error": str(refused)[:500]}
        self.known[verdict.said] = (admitted.acdc, disclosures(grant), verdict.registry)
        return 200, {"valid": verdict.state == "issued", "said": verdict.said,
                     "registry": verdict.registry}

    async def status(self, said: str) -> str:
        """What this verifier would say of the credential now, from the Observer alone."""
        if said not in self.known:
            return "unknown"
        acdc, blinders, _ = self.known[said]
        try:
            verdict = await self.box.verify_current_acdc(
                acdc=acdc, source=self.source, freshness=self.freshness,
                recency=party.RECENCY, blinders=blinders)
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
