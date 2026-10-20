"""A heti holder in a process of its own: ``python -m keri_bench.stacks.holder``.

Routes, each JSON: ``POST /deliver/<credential>`` takes the grant bytes and answers 202 at once
(the grant reached the holder's endpoint); ``POST /admit/<credential>`` admits it, which verifies
and stores it, and answers 200 when it is stored; ``POST /present/<credential>`` with
``{"verifier": aid}`` answers ``{"grant": base64}``, a presentation addressed to that verifier;
``POST /represent/<credential>`` does the same on the registry chain the Observer holds now.

Re-presenting is how a verifier learns that a credential on a blinded registry was revoked: the
disclosures a presentation carries read the chain as of that presentation, so a later revocation
needs newer ones, which only the holder can derive, from the salt the issuer sealed to it (heti
this.i, the AdmitResult.blinders node under @0ky6uwqz). The Observer-side blinder feed that would
let a verifier check status without the holder is the unlanded half of heti tick 7vhf.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from pathlib import Path

from ..synthetic import http
from . import announce, party, until_terminated


PLAIN = re.compile(r"[A-Za-z0-9_-]{1,64}")
"""A credential name the holder will use as a file name in its spool."""


class Holder:
    """With a ``spool``, every delivered grant is also kept on disk, so a restarted holder can
    admit it again and present what heti kept, rather than losing a credential to this
    wrapper's in-memory index (this.i 5ed29j5w)."""

    def __init__(self, box, facet, source=None, spool: Path | None = None) -> None:
        self.box, self.facet, self.source, self.spool = box, facet, source, spool
        self.grants: dict[str, bytes] = {}
        self.held: dict[str, object] = {}

    def _grant(self, credential: str) -> bytes | None:
        if credential not in self.grants and self.spool is not None \
                and PLAIN.fullmatch(credential) and (self.spool / credential).is_file():
            self.grants[credential] = (self.spool / credential).read_bytes()
        return self.grants.get(credential)

    async def _readmit(self, credential: str) -> None:
        """A grant on disk that this process never admitted was admitted before a restart;
        admit it again so the facet hands back what it retained."""
        grant = self._grant(credential)
        if credential in self.held or grant is None:
            return
        try:
            admitted = await self.facet.admit(grant=grant, recency=party.RECENCY)
        except Exception:
            return
        if hasattr(admitted, "acdc"):
            self.held[credential] = admitted

    async def handle(self, method: str, path: str, body: bytes):
        route, _, credential = path.strip("/").partition("/")
        if method == "GET" and route == "identity":
            return 200, {"aid": self.facet.aid, "oobi": self.facet.oobis[0]}
        if method == "POST" and route == "deliver":
            if self.spool is not None:
                if not PLAIN.fullmatch(credential):
                    return 400, {"code": "e.input.format.credential.f",
                                 "error": f"{credential[:80]!r} is not a plain credential "
                                          f"name, so it was not kept."}
                (self.spool / credential).write_bytes(body)
            self.grants[credential] = body
            return 202, {}
        if method == "POST" and route in ("present", "represent"):
            await self._readmit(credential)
        if method == "POST" and route == "admit" and self._grant(credential) is not None:
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
                                     blinders=admitted.blinders,
                                     recipient=json.loads(body)["verifier"])
            return 200, {"grant": base64.b64encode(grant).decode()}
        if method == "POST" and route == "represent" and credential in self.held:
            admitted = self.held[credential]
            try:
                tel = (await self.source.read(admitted.verdict.registry)).chain
                # No blinders=: the facet derives them for this chain from its own salt.
                grant = self.facet.grant(acdc=admitted.acdc, tel=tel,
                                         recipient=json.loads(body)["verifier"])
            except Exception as refused:
                return 409, {"code": party.code(refused), "error": str(refused)[:500]}
            return 200, {"grant": base64.b64encode(grant).decode()}
        return 404, {"error": f"There is no route {method} {path} for what this holder holds."}


async def serve(args) -> None:
    from heti.anchored.produce.observed import RegistrySource
    box, facet = await party.open_party(args)
    args.spool.mkdir(parents=True, exist_ok=True)
    holder = Holder(box, facet, RegistrySource(args.observer_url, aid=args.observer_aid),
                    args.spool)
    server = await http.serve(holder.handle, args.port)
    announce(url=f"http://127.0.0.1:{args.port}", aid=facet.aid, oobi=facet.oobis[0])
    async with server:
        await until_terminated()


def main(argv=None) -> None:
    parser = party.arguments(__doc__)
    parser.add_argument("--observer-url", required=True)
    parser.add_argument("--observer-aid", required=True)
    asyncio.run(serve(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
