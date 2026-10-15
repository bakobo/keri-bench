"""Real keripy witnesses in a process of their own: ``python -m keri_bench.stacks.witnesses``.

heti's ``demo.witnesses.Witnesses`` stands genuine witnesses up (real HTTP, receipts, escrow) in
temporary keystores. It advertises each witness at the address it binds, which a party learns
from the witness's signed location record when it resolves the OOBI; after that, every receipt
request goes to that address. So this subclass binds each witness on a port the harness chose and
advertises the harness's proxy in front of it instead, and receipts pass through the fault proxy
like every other link (this.i 0wcr2rrj). The loop is heti's ``Witnesses.start`` at heti
ed519a21c76554a1a345f0dc02a1a0bdf5623618, with the port and the advertised URL supplied.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from dataclasses import dataclass, field

from hio.base import doing
from keri.app import configing, habbing, storing
from keri.core import signing
from keri.help import helping

from heti.anchored.produce import declaring
from heti.demo.witnesses import Witnesses

from . import announce, until_terminated


@dataclass
class AdvertisedWitnesses(Witnesses):
    ports: list[int] = field(default_factory=list)
    advertised: list[str] = field(default_factory=list)

    async def start(self) -> AdvertisedWitnesses:
        for which, (port, url) in enumerate(zip(self.ports, self.advertised)):
            name = f"wit{which}"
            url = url.rstrip("/") + "/"
            configuration = configing.Configer(name=name, base="", temp=True, reopen=True,
                                               clear=False)
            configuration.put({name: {"dt": helping.nowIso8601(), "curls": [url]}})
            environment = habbing.Habery(
                name=name, base="", temp=True, cf=configuration,
                salt=signing.Salter(raw=self.seed + which.to_bytes(2, "big")).qb64)
            self._closers.append(environment)
            mailbox = storing.Mailboxer(name=name, base=environment.base, temp=True)
            self._mailboxes.append(mailbox)
            self._doers(alias=name, environment=environment, mailbox=mailbox, port=port)
            hab = environment.habByName(name)
            hab.psr.parse(bytearray(hab.makeDeclTags(tags=[declaring.TESTNET])))
            self.aids.append(hab.pre)
            self.urls[hab.pre] = url
        self._doist = doing.Doist(tock=0.03125, doers=self._pending)
        with contextlib.redirect_stdout(self.chatter):
            self._doist.enter()
        self._pump = asyncio.create_task(self._serve())
        await asyncio.sleep(0.2)
        return self


async def serve(ports: list[int], advertised: list[str]) -> None:
    pool = await AdvertisedWitnesses(count=len(ports), ports=ports, advertised=advertised).start()
    try:
        announce(oobis=[pool.oobi(aid) for aid in pool.aids], aids=pool.aids)
        await until_terminated()
    finally:
        await pool.stop()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, action="append", required=True)
    parser.add_argument("--advertise", action="append", required=True)
    args = parser.parse_args(argv)
    if len(args.port) != len(args.advertise):
        parser.error("give one --advertise per --port")
    asyncio.run(serve(args.port, args.advertise))


if __name__ == "__main__":
    main()
