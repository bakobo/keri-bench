"""Processes and adapters for real KERI stacks: imbu's issuer; heti's witnesses, Observer, holder
and verifier; bakobo/witness's Registrar. They need the ``stacks`` extra.

Each process prints exactly one JSON line on stdout once it is serving, naming its addresses,
then serves until SIGTERM. ``announce`` and ``until_terminated`` are copied from
bakobo/sedi-summit-nov26 harness/src/sedi_harness/procs/__init__.py at commit
ce3b91edc3b32ef0b12c03ec90924c3b1dbc9fed.
"""

import asyncio
import json
import signal
import sys


def announce(**fields) -> None:
    """The one line a parent reads: where this process can be reached."""
    sys.stdout.write(json.dumps(fields) + "\n")
    sys.stdout.flush()


async def until_terminated() -> None:
    """Return when SIGTERM or SIGINT arrives."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for which in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(which, stop.set)
    await stop.wait()
