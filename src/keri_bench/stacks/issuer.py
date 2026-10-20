"""imbu as the issuer, in a process of its own: ``python -m keri_bench.stacks.issuer``.

This is imbu's own ``Service`` and control plane, stood up the way ``imbu init``, ``imbu
learn-schema`` and ``imbu serve --registrar-url`` stand them up, with imbu's own
``RegistrarPublisher``. Every plain issuance gets a registry of its own (imbu @2tdzyh85), so
nothing is opened ahead of time. Adapted from bakobo/sedi-summit-nov26
harness/src/sedi_harness/procs/state.py at commit ce3b91edc3b32ef0b12c03ec90924c3b1dbc9fed
(``_provision``, ``start_listeners`` and ``serve``), for the imbu this repository pins.

Whatever control routes the pinned imbu has are what this process serves; it adds none.
The passcode comes from ``$BENCH_PASSCODE``, the publisher's seed from ``$BENCH_PUBLISHER_SEED``
(hex) and the home from ``$IMBU_HOME``.

A home that already exists is reopened rather than initialized, so a crash fault's restart is
imbu recovering its own home, operations and all (this.i 5ed29j5w).
"""

from __future__ import annotations

import argparse
import asyncio
import os

from . import announce, schema

PROFILE = "bench"


def _provision(home, passcode: str, *, learn: bool) -> tuple[str, tuple[str, ...]]:
    """What ``imbu learn-schema`` does, before the service holds the home; a reopened home
    learned it the first time."""
    from heti.anchored.produce import Lockbox, OwnFacet
    with Lockbox.open(path=home.path, passcode=passcode) as box:
        facet, = [f for f in box.facets() if isinstance(f, OwnFacet)]
        if learn:
            box.learn_schema(raw=schema.raw())
        return facet.aid, facet.oobis


async def start_listener(server) -> asyncio.Task:
    """Start a uvicorn server and return once it accepts, so the announcement is never early."""
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            raise SystemExit("imbu's control listener stopped before it started")
        await asyncio.sleep(0.02)
    return task


async def serve(args, passcode: str, issuer: str, oobis) -> None:
    import fiki
    import uvicorn
    from imbu.control import create_app
    from imbu.control.controller import parse_spec
    from imbu.control.service import Service
    from imbu.home import Home
    from imbu.publication import RegistrarPublisher

    service = Service(
        Home(PROFILE), passcode, configured_controllers=[parse_spec(s) for s in args.controller],
        issuer=issuer, schema=schema.said(),
        publisher=RegistrarPublisher(args.registrar_url, key=fiki.Key.from_seed(
            bytes.fromhex(os.environ["BENCH_PUBLISHER_SEED"]))))
    with service:
        server = uvicorn.Server(uvicorn.Config(create_app(service), host="127.0.0.1",
                                               port=args.control_port, log_level="warning"))
        task = await start_listener(server)
        announce(issuer=issuer, oobis=list(oobis),
                 control=f"http://127.0.0.1:{args.control_port}")
        await task  # uvicorn exits on SIGTERM; then the service closes


def main(argv=None) -> None:
    from heti.anchored.produce import new_recovery_code
    from imbu.agent import InProcessAgent
    from imbu.home import Home

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--witness", action="append", required=True)
    parser.add_argument("--toad", type=int, required=True)
    parser.add_argument("--registrar-url", required=True)
    parser.add_argument("--controller", action="append", required=True)
    parser.add_argument("--control-port", type=int, required=True)
    args = parser.parse_args(argv)
    passcode = os.environ["BENCH_PASSCODE"]
    home = Home(PROFILE)
    fresh = not home.is_home()
    if fresh:
        InProcessAgent(home, passcode).init(recovery=new_recovery_code(),
                                            witnesses=args.witness, toad=args.toad)
    issuer, oobis = _provision(home, passcode, learn=fresh)
    asyncio.run(serve(args, passcode, issuer, oobis))


if __name__ == "__main__":
    main()
