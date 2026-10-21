"""The proofing agent and the citizens of the SEDI bulk ceremony, in a process of their own:
``python -m keri_bench.stacks.citizens`` (this.i 4f987y88).

The proofing agent is a witnessed facet in one lockbox; each citizen's SEDI Management AID (SMAID)
is a witnessed facet in another, as the summit harness stands them up (bakobo/sedi-summit-nov26
harness/src/sedi_harness/acts.py, ``proof`` and ``bulk``, at ce3b91e). Routes, each JSON:

- ``GET /identity``: ``{"proofer": {"aid", "oobi"}, "citizens": [{"aid", "oobi"}, ...]}``;
- every POST carries ``{"token": ...}``, the run's ``$BENCH_CITIZENS_TOKEN``, or is refused;
- ``POST /ceremony/<i>``: the proofing agent signs an IAR for citizen ``i`` and the citizen
  anchors the IAR's nonce in its KEL; answers ``{"iar": base64, "said": the IAR's SAID}``;
- ``POST /sign/<i>`` with ``{"method", "url", "body": base64 or null}``: the headers citizen
  ``i``'s SMAID signs that request with (RFC 9421, through heti).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hmac
import json
import os
from pathlib import Path

from ..synthetic import http
from . import announce, party, sedi, until_terminated


def acdc_said(stream: bytes) -> str:
    """The SAID of the ACDC a signed stream carries, its genus prefix skipped."""
    from keri.core import Counter, serdering
    from keri.kering import Version
    genus = bytes(Counter.makeGVC(version=Version))
    body = stream[len(genus):] if stream.startswith(genus) else stream
    return serdering.SerderACDC(raw=bytes(body)).said


E_TOKEN = "e.auth.citizens.token.f"


class Citizens:
    """Signs and proofs only for whoever holds the run's token, so no other process on a shared
    host can have a citizen sign a request of its choosing (hostile review, finding 1)."""

    def __init__(self, proofer, citizens, *, token: str) -> None:
        self.proofer, self.citizens, self.token = proofer, citizens, token

    def _asked(self, body: bytes) -> dict | None:
        try:
            asked = json.loads(body)
        except ValueError:
            return None
        if not isinstance(asked, dict) or not hmac.compare_digest(
                str(asked.get("token", "")).encode(), self.token.encode()):
            return None
        return asked

    async def handle(self, method: str, path: str, body: bytes):
        route, _, which = path.strip("/").partition("/")
        if method == "GET" and route == "identity":
            return 200, {"proofer": {"aid": self.proofer.aid, "oobi": self.proofer.oobis[0]},
                         "citizens": [{"aid": c.aid, "oobi": c.oobis[0]}
                                      for c in self.citizens]}
        if method != "POST" or not which.isdigit() or int(which) >= len(self.citizens):
            return 404, {"error": f"There is no route {method} {path}."}
        if route not in ("ceremony", "sign"):
            return 404, {"error": f"There is no route {method} {path}."}
        asked = self._asked(body)
        if asked is None:
            return 403, {"code": E_TOKEN, "error": "The request does not carry this run's "
                         "token, so nothing was signed."}
        citizen = self.citizens[int(which)]
        if route == "ceremony":
            from keri.core import Noncer
            nonce = Noncer().qb64
            iar = self.proofer.issue_signed(schema=sedi.said("iar"), issuee=citizen.aid,
                                            nonce=nonce, attributes=sedi.iar_attributes())
            await citizen.anchor(seals=({"nd": nonce},))
            return 200, {"iar": base64.b64encode(iar).decode(), "said": acdc_said(iar)}
        payload = base64.b64decode(asked["body"]) if asked.get("body") else None
        return 200, {"headers": citizen.sign_request(method=asked["method"], url=asked["url"],
                                                     body=payload)}


async def _lockbox(path: Path, witnesses: list[str], toad: int, count: int):
    from heti.anchored.produce import Lockbox, new_recovery_code
    box = Lockbox.create(path=str(path), passcode=os.environ["BENCH_PASSCODE"],
                         recovery=new_recovery_code(), timeout=party.LOCKBOX_TIMEOUT)
    known = [await box.learn_witness(oobi=oobi) for oobi in witnesses]
    for name in ("iar", "core", "residence"):
        box.learn_schema(raw=sedi.raw(name))
    return box, [await box.mint(witnesses=known, toad=toad) for _ in range(count)]


async def serve(args) -> None:
    args.store.mkdir(parents=True, exist_ok=True)
    _, (proofer,) = await _lockbox(args.store / "proofer", args.witness, args.toad, 1)
    _, citizens = await _lockbox(args.store / "citizens", args.witness, args.toad,
                                 args.citizens)
    server = await http.serve(Citizens(proofer, citizens,
                                       token=os.environ["BENCH_CITIZENS_TOKEN"]).handle,
                              args.port)
    announce(url=f"http://127.0.0.1:{args.port}")
    async with server:
        await until_terminated()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--witness", action="append", required=True)
    parser.add_argument("--toad", type=int, required=True)
    parser.add_argument("--citizens", type=int, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--store", type=Path, required=True)
    asyncio.run(serve(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
