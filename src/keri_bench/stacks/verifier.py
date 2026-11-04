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

With ``--stages`` it is the timed verifier (this.i 3jeks5st): heti's stage functions are wrapped
before the lockbox opens (``stages.py``), and every ``/verify`` attempt, accepted or refused,
appends one JSON line of stage times to ``stages.jsonl`` in the spool, as does opening the
lockbox, whose issuer OOBIs are the verifier's first meetings.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from pathlib import Path

from ..synthetic import http
from . import announce, party, stages, until_terminated

REVOKED = "e.state.conflict.acdc.revoked."
KNOWN = "known.json"
STAGES = "stages.jsonl"


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


def _grant(grant: bytes):
    from keri.core import Counter, serdering
    from keri.kering import Version
    genus = bytes(Counter.makeGVC(version=Version))
    body = grant[len(genus):] if grant.startswith(genus) else grant
    return serdering.SerderKERI(raw=bytearray(body)).sad


def sender(grant: bytes) -> str | None:
    """Who signed a grant, its ``i``, so the timed verifier can tell the presenter's key state
    from the issuer's; None if it cannot be read. admit checks the signatures against it."""
    try:
        return _grant(grant).get("i")
    except Exception:
        return None


def presented(grant: bytes) -> list | None:
    """The credential SAIDs a grant says it conveys, its signed ``a.o``; None if it cannot be
    read. admit checks the claim against the bytes it carries, so a lie here is refused there."""
    try:
        return _grant(grant).get("a", {}).get("o")
    except Exception:
        return None


class Verifier:
    def __init__(self, box, facet, source, freshness: float, spool: Path | None = None, *,
                 timed: bool = False) -> None:
        self.box, self.facet, self.source, self.freshness = box, facet, source, freshness
        self.timed = timed
        self.met: set[str] = set()
        self.known: dict[str, str] = {}
        """Each credential this verifier has accepted, by SAID, and its registry; kept in the
        spool too, so a restarted verifier still knows what it verified (this.i 5ed29j5w)."""
        self.spool = spool
        if spool is not None and (spool / KNOWN).is_file():
            self.known = json.loads((spool / KNOWN).read_text())

    async def handle(self, method: str, path: str, body: bytes):
        path = path.partition("?")[0]
        route, _, name = path.strip("/").partition("/")
        if method == "GET" and route == "identity":
            return 200, {"aid": self.facet.aid, "oobi": self.facet.oobis[0]}
        if method == "POST" and route == "verify":
            if self.timed:
                return await self.timed_verify(name, json.loads(body))
            return await self.verify(json.loads(body))
        if method == "POST" and route == "status":
            try:
                request = json.loads(body)
            except ValueError:
                request = None
            if not isinstance(request, dict) or not isinstance(request.get("grant"), str):
                return 400, {"code": "e.input.format.status.f",
                             "error": "A status request is a JSON presentation."}
            return 200, {"status": await self.status(name, request)}
        if method == "POST" and route == "sync":
            registries = sorted(set(self.known.values()))
            events = 0
            for registry in registries:
                events += count_events((await self.source.read(registry)).chain)
            return 200, {"registries": len(registries), "events": events}
        return 404, {"error": f"There is no route {method} {path}."}

    async def timed_verify(self, credential: str, request: dict):
        try:
            holder = sender(base64.b64decode(request["grant"], validate=True))
        except Exception:
            holder = None  # verify refuses it by name below
        attempt = stages.Attempt(credential, holder=holder)
        with attempt.active():
            status, answer = await self.verify(request, attempt)
        stages.write(self.spool / STAGES, attempt.record(
            outcome="accepted" if status == 200 else "refused", code=answer.get("code")))
        return status, answer

    async def verify(self, request: dict, attempt: stages.Attempt | None = None):
        phase = attempt.phase if attempt is not None else _untimed
        try:
            if request["oobi"] not in self.met:
                await self.box.add_other(oobi=request["oobi"])
                self.met.add(request["oobi"])
            grant = base64.b64decode(request["grant"], validate=True)
            with phase("admit"):
                admitted = await self.facet.admit(grant=grant, recency=party.RECENCY)
            # A verifier is not retained as the credential's holder, so the disclosures admit
            # verified with are the only copy it has (heti AdmitResult.blinders, @9zc89rwz).
            with phase("current"):
                verdict = await self.box.verify_current_acdc(
                    acdc=admitted.acdc, source=self.source, freshness=self.freshness,
                    recency=party.RECENCY, blinders=admitted.blinders)
        except Exception as refused:
            return 409, {"valid": False, "code": party.code(refused),
                         "error": str(refused)[:500]}
        self.known[verdict.said] = verdict.registry
        if self.spool is not None:
            (self.spool / KNOWN).write_text(json.dumps(self.known))
        return 200, {"valid": verdict.state == "issued", "said": verdict.said,
                     "registry": verdict.registry}

    async def status(self, said: str, request: dict) -> str:
        """What this verifier says of the credential now, from a fresh presentation of it."""
        if said not in self.known:
            return "unknown"
        try:
            grant = base64.b64decode(request["grant"], validate=True)
            # Which credential the presentation is of, before admit judges it: admit refuses a
            # revoked credential, and that refusal must not be read as this one's revocation.
            if presented(grant) != [said]:
                return "unknown"
            admitted = await self.facet.admit(grant=grant, recency=party.RECENCY)
            if admitted.verdict.said != said:
                return "unknown"
            verdict = await self.box.verify_current_acdc(
                acdc=admitted.acdc, source=self.source, freshness=self.freshness,
                recency=party.RECENCY, blinders=admitted.blinders)
        except Exception as refused:
            return "revoked" if party.code(refused).startswith(REVOKED) else "unknown"
        return "valid" if verdict.state == "issued" else "unknown"


@contextlib.contextmanager
def _untimed(name: str):
    yield


async def serve(args) -> None:
    from heti.anchored.produce.observed import RegistrySource
    args.spool.mkdir(parents=True, exist_ok=True)
    source = RegistrySource(args.observer_url, aid=args.observer_aid)
    if args.stages:
        stages.install()
        source = stages.TimedSource(source)
        opening = stages.Attempt("lockbox-open", holder=None)
        with opening.active():
            box, facet = await party.open_party(args)
        stages.write(args.spool / STAGES, opening.record(outcome="opened", code=None))
    else:
        box, facet = await party.open_party(args)
    verifier = Verifier(box, facet, source, args.freshness, args.spool, timed=args.stages)
    server = await http.serve(verifier.handle, args.port)
    announce(url=f"http://127.0.0.1:{args.port}", aid=facet.aid, oobi=facet.oobis[0])
    async with server:
        await until_terminated()


def main(argv=None) -> None:
    parser = party.arguments(__doc__)
    parser.add_argument("--observer-url", required=True)
    parser.add_argument("--observer-aid", required=True)
    parser.add_argument("--freshness", type=float, required=True)
    parser.add_argument("--stages", action="store_true",
                        help="time each verification by stage into the spool (this.i 3jeks5st)")
    asyncio.run(serve(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
