"""An independent heti verifier in a process of its own: ``python -m keri_bench.stacks.verifier``.

It is not the issuer's code path (this.i 27mjdj7q): its own lockbox admits the holder's
presentation, then checks the credential is current against the Observer, which is the only
place it takes TEL state from. Routes, each JSON:

- ``POST /verify/<credential>`` with ``{"grant": base64, "oobi": holder OOBI}``: 200 with
  ``{"valid": true, "said", "registry"}``, or 409 with the refusal's code;
- ``GET /status/<said>?registry=<regid>``: ``{"status": "valid"|"revoked"|"unknown"}`` from the
  Observer's chain for that registry;
- ``POST /sync/<name>?registry=<regid>``: reads and parses the registry's whole chain, as a cold
  verifier's first synchronization does, and answers how many events it held.
"""

from __future__ import annotations

import asyncio
import base64
import json
from urllib.parse import parse_qs

from ..synthetic import http
from . import announce, party, until_terminated


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


class Verifier:
    def __init__(self, box, facet, source, freshness: float) -> None:
        self.box, self.facet, self.source, self.freshness = box, facet, source, freshness
        self.met: set[str] = set()

    async def handle(self, method: str, path: str, body: bytes):
        path, _, query = path.partition("?")
        route, _, name = path.strip("/").partition("/")
        params = {k: v[0] for k, v in parse_qs(query).items()}
        if method == "GET" and route == "identity":
            return 200, {"aid": self.facet.aid, "oobi": self.facet.oobis[0]}
        if method == "POST" and route == "verify":
            return await self.verify(json.loads(body))
        if method == "GET" and route == "status" and "registry" in params:
            return 200, {"status": await self.status(name, params["registry"])}
        if method == "POST" and route == "sync" and "registry" in params:
            observed = await self.source.read(params["registry"])
            return 200, {"events": states(observed.chain)[1], "gap": observed.gap}
        return 404, {"error": f"There is no route {method} {path}."}

    async def verify(self, request: dict):
        try:
            if request["oobi"] not in self.met:
                await self.box.add_other(oobi=request["oobi"])
                self.met.add(request["oobi"])
            admitted = await self.facet.admit(grant=base64.b64decode(request["grant"],
                                                                     validate=True),
                                              recency=party.RECENCY)
            verdict = await self.box.verify_current_acdc(
                acdc=admitted.acdc, source=self.source, freshness=self.freshness,
                recency=party.RECENCY)
        except Exception as refused:
            return 409, {"valid": False, "code": party.code(refused),
                         "error": str(refused)[:500]}
        return 200, {"valid": verdict.state == "issued", "said": verdict.said,
                     "registry": verdict.registry}

    async def status(self, said: str, registry: str) -> str:
        try:
            observed = await self.source.read(registry)
        except Exception:
            return "unknown"
        if observed.gap:
            return "unknown"
        state = states(observed.chain)[0].get(said)
        return {"issued": "valid", "revoked": "revoked"}.get(state, "unknown")


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
