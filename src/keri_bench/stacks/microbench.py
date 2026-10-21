"""The primitive microbenchmark (test plan, HSM sensitivity; this.i 9n80j7ph):
``python -m keri_bench.stacks.microbench --out <file>``.

It times keripy's own ``Signer.sign``, ``Verfer.verify`` and a Blake3-256 ``Diger``, the calls the
stack under test makes, so the cost includes keripy's CESR wrapping, which is what an HSM would
replace. One core, one process, each call timed on its own with the harness's monotonic clock,
percentiles through the one named definition. A signature that does not verify stops the run,
because a benchmark of a broken primitive is no benchmark.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from importlib.metadata import version
from pathlib import Path

from keri.core import Diger, MtrDex
from keri.core.signing import Salter

from ..errors import BenchError
from ..provenance import host_fingerprint
from ..stats import Histogram

E_RANGE = "e.input.range.microbench.f"
E_VERIFY = "e.self.microbench.verify.f"
SIZES = (32, 256, 1024, 4096)
"""Payload bytes: a digest, a small event, a typical KEL or TEL event, a large ACDC."""


def _verify_ok(verfer, sig, data: bytes) -> bool:
    return verfer.verify(sig=sig, ser=data)


def _timed(call, iterations: int) -> dict:
    histogram = Histogram()
    began = time.monotonic_ns()
    for _ in range(iterations):
        start = time.monotonic_ns()
        call()
        histogram.record(time.monotonic_ns() - start)
    total = time.monotonic_ns() - began
    return {"n": iterations, "p50_ns": histogram.percentile(50),
            "p99_ns": histogram.percentile(99), "max_ns": histogram.max,
            "per_s": iterations * 1e9 / total}


def bench(*, sizes=SIZES, iterations: int = 2000) -> dict:
    if not sizes or iterations < 1 or any(size < 1 for size in sizes):
        raise BenchError(E_RANGE, f"A microbenchmark needs at least one positive payload size "
                         f"and at least one iteration, not sizes {list(sizes)} and "
                         f"{iterations} iterations.")
    signer = Salter().signer(path="0", transferable=True, temp=True)
    operations: dict[str, dict] = {"ed25519_sign": {}, "ed25519_verify": {}, "blake3_256": {}}
    for size in sizes:
        data = os.urandom(size)
        sig = signer.sign(ser=data)
        if not _verify_ok(signer.verfer, sig.raw, data):
            raise BenchError(E_VERIFY, f"keripy's Ed25519 signature over {size} bytes did not "
                             f"verify on this host, so nothing was timed.")
        operations["ed25519_sign"][str(size)] = _timed(lambda: signer.sign(ser=data),
                                                       iterations)
        operations["ed25519_verify"][str(size)] = _timed(
            lambda: signer.verfer.verify(sig=sig.raw, ser=data), iterations)
        operations["blake3_256"][str(size)] = _timed(
            lambda: Diger(ser=data, code=MtrDex.Blake3_256), iterations)
    return {"operations": operations, "sizes": list(sizes), "iterations": iterations,
            "keri": version("keri"), "host": host_fingerprint(), "nice": os.nice(0)}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--size", type=int, action="append",
                        help="a payload size in bytes; repeat it; the default is "
                             f"{list(SIZES)}")
    args = parser.parse_args(argv)
    result = bench(sizes=tuple(args.size or SIZES), iterations=args.iterations)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
