"""A heti holder in a process of its own: ``python -m keri_bench.stacks.holder``.

Routes, each JSON: ``POST /deliver/<credential>`` takes the grant bytes and answers 202 at once
(the grant reached the holder's endpoint); ``POST /admit/<credential>`` admits it, which verifies
and stores it, and answers 200 when it is stored; ``POST /present/<credential>`` with
``{"verifier": aid}`` answers ``{"grant": base64}``, a presentation addressed to that verifier.
"""

from __future__ import annotations

import asyncio
import base64
import json

from ..synthetic import http
from . import announce, party, until_terminated


class Holder:
    def __init__(self, box, facet) -> None:
        self.box, self.facet = box, facet
        self.grants: dict[str, bytes] = {}
        self.held: dict[str, object] = {}

    async def handle(self, method: str, path: str, body: bytes):
        route, _, credential = path.strip("/").partition("/")
        if method == "GET" and route == "identity":
            return 200, {"aid": self.facet.aid, "oobi": self.facet.oobis[0]}
        if method == "POST" and route == "deliver":
            self.grants[credential] = body
            return 202, {}
        if method == "POST" and route == "admit" and credential in self.grants:
            try:
                admitted = await self.facet.admit(grant=self.grants[credential],
                                                  recency=party.RECENCY)
            except Exception as refused:
                return 409, {"code": party.code(refused), "error": str(refused)[:500]}
            if not hasattr(admitted, "acdc"):
                return 409, {"code": "held", "error": "The grant is held: its sender's "
                             "signing threshold is not met yet."}
            self.held[credential] = admitted
            return 200, {"said": admitted.verdict.said, "registry": admitted.verdict.registry}
        if method == "POST" and route == "present" and credential in self.held:
            admitted = self.held[credential]
            grant = self.facet.grant(acdc=admitted.acdc, tel=admitted.tel,
                                     recipient=json.loads(body)["verifier"])
            return 200, {"grant": base64.b64encode(grant).decode()}
        return 404, {"error": f"There is no route {method} {path} for what this holder holds."}


async def serve(args) -> None:
    box, facet = await party.open_party(args)
    holder = Holder(box, facet)
    server = await http.serve(holder.handle, args.port)
    announce(url=f"http://127.0.0.1:{args.port}", aid=facet.aid, oobi=facet.oobis[0])
    async with server:
        await until_terminated()


def main(argv=None) -> None:
    asyncio.run(serve(party.arguments(__doc__).parse_args(argv)))


if __name__ == "__main__":
    main()
