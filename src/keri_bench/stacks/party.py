"""What a heti holder and a heti verifier share: a lockbox with one witnessed facet that knows the
issuer and the bench's schema, served over the synthetic stack's small JSON server.

The calls follow the summit harness's ``Party`` (bakobo/sedi-summit-nov26
harness/src/sedi_harness/stage.py:115-143 at ce3b91e), updated for the heti this repository pins:
``Lockbox.create`` takes a recovery code.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from . import schema

RECENCY = 60
LOCKBOX_TIMEOUT = 20.0


def arguments(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--lockbox", type=Path, required=True)
    parser.add_argument("--witness", action="append", required=True)
    parser.add_argument("--toad", type=int, required=True)
    parser.add_argument("--issuer-oobi", action="append", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--spool", type=Path, required=True,
                        help="where the wrapper keeps what a restart must not forget")
    return parser


async def open_party(args):
    """A new lockbox with one facet witnessed to toad, knowing the issuer and the schema; or,
    after a crash, the same lockbox reopened, which already knows them (this.i 5ed29j5w)."""
    from heti.anchored.produce import Lockbox, OwnFacet, new_recovery_code
    if args.lockbox.exists():
        box = Lockbox.open(path=str(args.lockbox), passcode=os.environ["BENCH_PASSCODE"],
                           timeout=LOCKBOX_TIMEOUT)
        facet, = [f for f in box.facets() if isinstance(f, OwnFacet)]
        return box, facet
    box = Lockbox.create(path=str(args.lockbox), passcode=os.environ["BENCH_PASSCODE"],
                         recovery=new_recovery_code(), timeout=LOCKBOX_TIMEOUT)
    witnesses = [await box.learn_witness(oobi=oobi) for oobi in args.witness]
    facet = await box.mint(witnesses=witnesses, toad=args.toad)
    for oobi in args.issuer_oobi:
        await box.add_other(oobi=oobi)
    box.learn_schema(raw=schema.raw())
    return box, facet


def code(failure: Exception) -> str:
    """A heti refusal's stable code, or the exception's name when it has none."""
    return getattr(failure, "code", None) or type(failure).__name__
