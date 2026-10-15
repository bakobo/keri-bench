"""imbu as the issuer, in a process of its own: ``python -m keri_bench.stacks.issuer``.

This is imbu's own ``Service`` and control plane, stood up the way ``imbu init`` and ``imbu serve``
stand them up, with one difference the CLI cannot express yet: the publication outbox is pointed
at the Registrar through an injected publisher, because ``imbu serve`` wires none (imbu tick
~3p7h). Adapted from bakobo/sedi-summit-nov26 harness/src/sedi_harness/procs/state.py at commit
ce3b91edc3b32ef0b12c03ec90924c3b1dbc9fed (``RegistrarPublisher``, ``_provision``,
``start_listeners`` and ``serve``), for the imbu this repository pins: ``init`` now takes a
recovery code, and only the bench's one schema is learned.

Whatever control routes the pinned imbu has are what this process serves; it adds none.
The passcode comes from ``$BENCH_PASSCODE``, the publisher's seed from ``$BENCH_PUBLISHER_SEED``
(hex) and the home from ``$IMBU_HOME``.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os

from . import announce, schema

PROFILE = "bench"
REGISTRY = "legacy"


class AcknowledgementMismatch(RuntimeError):
    """The Registrar acknowledged a snapshot other than the one sent."""

    code = "e.state.conflict.registrar.ack.r"


class RegistrarPublisher:
    """imbu's publisher seam, speaking to the Registrar (witness docs/registrar.md): each
    publication RFC 9421-signed by the publisher key the Registrar was started with."""

    def __init__(self, url: str, *, key) -> None:
        self.url, self.key = f"{url.rstrip('/')}/v1/registrar/publication", key

    async def publish(self, snapshot) -> str:
        import fiki
        import httpx
        body = json.dumps({
            "registry": snapshot.registry, "issuer": snapshot.issuer, "digest": snapshot.digest,
            "chain": base64.b64encode(snapshot.chain).decode(),
            "kel": base64.b64encode(snapshot.kel).decode()}).encode()
        headers = fiki.sign_request(key=self.key, method="POST", url=self.url, body=body)
        async with httpx.AsyncClient(timeout=10) as client:
            answer = await client.post(self.url, content=body,
                                       headers={**headers, "Content-Type": "application/json"})
            answer.raise_for_status()
            acknowledged = answer.json()["digest"]
        if acknowledged != snapshot.digest:
            raise AcknowledgementMismatch(
                f"{AcknowledgementMismatch.code}: the Registrar acknowledged {acknowledged!r} "
                f"for registry {snapshot.registry}, not the snapshot {snapshot.digest!r} sent")
        return acknowledged


async def _provision(home, passcode: str) -> tuple[str, tuple[str, ...], str]:
    """What ``imbu learn-schema`` and ``imbu open-registry`` do, before the service holds the
    home."""
    from heti.anchored.produce import Lockbox
    with Lockbox.open(path=home.path, passcode=passcode) as box:
        facet, = [f for f in box.facets() if hasattr(f, "create_registry")]
        box.learn_schema(raw=schema.raw())
        registry = await facet.create_registry(alias=REGISTRY)
        return facet.aid, facet.oobis, registry.regid


async def serve(args, passcode: str, issuer: str, oobis, registry: str) -> None:
    import fiki
    import uvicorn
    from imbu.control import create_app
    from imbu.control.controller import parse_spec
    from imbu.control.service import Service
    from imbu.home import Home

    service = Service(
        Home(PROFILE), passcode, configured_controllers=[parse_spec(s) for s in args.controller],
        issuer=issuer, registry=REGISTRY, schema=schema.said(),
        publisher=RegistrarPublisher(args.registrar, key=fiki.Key.from_seed(
            bytes.fromhex(os.environ["BENCH_PUBLISHER_SEED"]))))
    with service:
        server = uvicorn.Server(uvicorn.Config(create_app(service), host="127.0.0.1",
                                               port=args.control_port, log_level="warning"))
        task = asyncio.create_task(server.serve())
        while not server.started:
            if task.done():
                raise SystemExit("imbu's control listener stopped before it started")
            await asyncio.sleep(0.02)
        announce(issuer=issuer, oobis=list(oobis), registry=registry,
                 control=f"http://127.0.0.1:{args.control_port}")
        await task  # uvicorn exits on SIGTERM; then the service closes


def main(argv=None) -> None:
    from heti.anchored.produce import new_recovery_code
    from imbu.agent import InProcessAgent
    from imbu.home import Home

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--witness", action="append", required=True)
    parser.add_argument("--toad", type=int, required=True)
    parser.add_argument("--registrar", required=True)
    parser.add_argument("--controller", action="append", required=True)
    parser.add_argument("--control-port", type=int, required=True)
    args = parser.parse_args(argv)
    passcode = os.environ["BENCH_PASSCODE"]
    home = Home(PROFILE)
    InProcessAgent(home, passcode).init(recovery=new_recovery_code(), witnesses=args.witness,
                                        toad=args.toad)
    issuer, oobis, registry = asyncio.run(_provision(home, passcode))
    asyncio.run(serve(args, passcode, issuer, oobis, registry))


if __name__ == "__main__":
    main()
