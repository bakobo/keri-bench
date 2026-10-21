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

With ``--signer external`` the principal's keys are held by heti's ThrottledSigner over a
SoftwareSigner whose salt is ``$BENCH_SIGNER_SALT``, at ``--signer-latency-ms`` per operation and
at most ``--signer-rate`` starts per second; it mints the principal on a fresh home, is attached
on every start (imbu ``Service.attach_signer``), and its record is served at ``GET /signer`` on
``--signer-port`` (stacks/signer.py, this.i 2p26rn9h).

With ``--bulk`` imbu serves the SEDI proofed bulk ceremony instead of plain issuance (this.i
4f987y88): it learns the SEDI schemas, trusts ``--proofer`` as the IAR's proofing agent, and on a
fresh home a second facet in its own lockbox, ``utah-root``, issues the principal the Utah agent
credential that every Core's far node names, so imbu reads that registry locally. The agent
credential is announced as ``agent`` (``said``, and base64 ``acdc`` and ``chain``).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os

from . import announce, schema

PROFILE = "bench"
ROOT = "utah-root"
IAR_RECENCY = 60.0
IAR_MAX_AGE = 3600.0
CREDENTIAL_VALIDITY = 365 * 24 * 3600


def _provision(home, passcode: str, *, learn: bool) -> tuple[str, tuple[str, ...]]:
    """What ``imbu learn-schema`` does, before the service holds the home; a reopened home
    learned it the first time."""
    from heti.anchored.produce import Lockbox, OwnFacet
    with Lockbox.open(path=home.path, passcode=passcode) as box:
        facet, = [f for f in box.facets() if isinstance(f, OwnFacet) and f.alias != ROOT]
        if learn:
            box.learn_schema(raw=schema.raw())
        return facet.aid, facet.oobis


async def _provision_bulk(home, passcode: str, *, recovery: str | None, witnesses,
                          toad: int, principal: str) -> dict:
    """The SEDI schemas and the Utah agent credential, on a fresh home (``recovery`` given);
    on a reopened one, the agent credential read back from the root facet's issued record."""
    from heti.anchored.produce import Lockbox, OwnFacet

    from . import sedi
    with Lockbox.open(path=home.path, passcode=passcode) as box:
        if recovery is not None:
            for name in ("iar", "core", "residence", "agency"):
                box.learn_schema(raw=sedi.raw(name))
            known = [await box.learn_witness(oobi=oobi) for oobi in witnesses]
            root = await box.mint(witnesses=known, toad=toad, alias=ROOT, recovery=recovery)
            await root.issue(schema=sedi.said("agency"), attributes=sedi.AGENCY_ATTRIBUTES,
                             issuee=principal)
        root, = [f for f in box.facets() if isinstance(f, OwnFacet) and f.alias == ROOT]
        agent, = root.credentials(role="issued")
        return {"said": agent.said, "acdc": base64.b64encode(agent.acdc).decode(),
                "chain": base64.b64encode(agent.tel).decode()}


def _bulk_service(args) -> dict:
    """What a bulk home's Service is configured with beyond plain issuance."""
    if not args.bulk:
        return {"schema": schema.said()}
    from . import sedi
    return {"schema": sedi.said("core"), "bulk_residence_schema": sedi.said("residence"),
            "iar_schema": sedi.said("iar"), "iar_proofers": (args.proofer,),
            "iar_recency": IAR_RECENCY, "iar_max_age": IAR_MAX_AGE,
            "credential_validity": CREDENTIAL_VALIDITY}


async def start_listener(server) -> asyncio.Task:
    """Start a uvicorn server and return once it accepts, so the announcement is never early."""
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            raise SystemExit("imbu's control listener stopped before it started")
        await asyncio.sleep(0.02)
    return task


def external_signer(args, *, fresh: bool):
    """The counting, throttled signer that holds the principal's keys, or None for the lockbox.
    Key 0 of the run's salt, whether it mints the principal now or meets it again on restart."""
    if args.signer != "external":
        return None
    from heti.anchored.produce import SoftwareSigner

    from .signer import counting
    inner = SoftwareSigner(salt=os.environ["BENCH_SIGNER_SALT"], index=None if fresh else 0)
    return counting(inner, latency_ms=args.signer_latency_ms, rate=args.signer_rate)


async def serve(args, passcode: str, issuer: str, oobis, signer=None,
                agent: dict | None = None) -> None:
    import fiki
    import uvicorn
    from imbu.control import create_app
    from imbu.control.controller import parse_spec
    from imbu.control.service import Service
    from imbu.home import Home
    from imbu.publication import RegistrarPublisher

    service = Service(
        Home(PROFILE), passcode, configured_controllers=[parse_spec(s) for s in args.controller],
        issuer=issuer, **_bulk_service(args),
        publisher=RegistrarPublisher(args.registrar_url, key=fiki.Key.from_seed(
            bytes.fromhex(os.environ["BENCH_PUBLISHER_SEED"]))))
    with service:
        if signer is not None:
            from ..synthetic import http
            from .signer import handler
            await service.attach_signer(aid=issuer, signer=signer)
            await http.serve(handler(signer), args.signer_port)
        server = uvicorn.Server(uvicorn.Config(create_app(service), host="127.0.0.1",
                                               port=args.control_port, log_level="warning"))
        task = await start_listener(server)
        announce(issuer=issuer, oobis=list(oobis),
                 control=f"http://127.0.0.1:{args.control_port}",
                 **({"agent": agent} if agent is not None else {}))
        # uvicorn exits on SIGTERM; then the service closes, and the process's exit closes the
        # signer's record port with it.
        await task


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
    parser.add_argument("--signer", choices=("lockbox", "external"), default="lockbox")
    parser.add_argument("--signer-latency-ms", type=float)
    parser.add_argument("--signer-rate", type=float)
    parser.add_argument("--signer-port", type=int)
    parser.add_argument("--bulk", action="store_true")
    parser.add_argument("--proofer")
    args = parser.parse_args(argv)
    if args.signer == "external" and args.signer_port is None:
        parser.error("an external signer needs --signer-port for its record")
    if args.bulk and not args.proofer:
        parser.error("the bulk ceremony needs --proofer, the IAR's proofing agent")
    passcode = os.environ["BENCH_PASSCODE"]
    home = Home(PROFILE)
    fresh = not home.is_home()
    signer = external_signer(args, fresh=fresh)
    recovery = new_recovery_code()
    if fresh:
        InProcessAgent(home, passcode).init(recovery=recovery, witnesses=args.witness,
                                            toad=args.toad, signer=signer)
    issuer, oobis = _provision(home, passcode, learn=fresh)
    agent = asyncio.run(_provision_bulk(
        home, passcode, recovery=recovery if fresh else None, witnesses=args.witness,
        toad=args.toad, principal=issuer)) if args.bulk else None
    asyncio.run(serve(args, passcode, issuer, oobis, signer, agent))


if __name__ == "__main__":
    main()
