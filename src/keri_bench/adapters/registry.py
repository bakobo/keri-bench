"""Adapter names a scenario may use, mapped to where each class lives.

Classes are imported only when a scenario names them, so the harness core and the synthetic
stack run without any real stack's packages installed.
"""

from __future__ import annotations

import importlib

from ..errors import BenchError
from .base import Adapter

ADAPTERS = {
    "synthetic.issuer": "keri_bench.synthetic.adapters:SyntheticIssuer",
    "synthetic.state_distribution": "keri_bench.synthetic.adapters:SyntheticStateDistribution",
    "synthetic.witness_pool": "keri_bench.synthetic.adapters:SyntheticWitnessPool",
    "synthetic.holder": "keri_bench.synthetic.adapters:SyntheticHolder",
    "synthetic.verifier": "keri_bench.synthetic.adapters:SyntheticVerifier",
}

E_UNKNOWN = "e.input.range.scenario.adapter.f"
E_UNAVAILABLE = "e.feature.unsupported.adapter.f"


def lookup(name: str) -> type[Adapter]:
    if name not in ADAPTERS:
        raise BenchError(E_UNKNOWN, f"The scenario names the adapter {name!r}; the known "
                         f"adapters are {sorted(ADAPTERS)}.")
    module, _, attribute = ADAPTERS[name].partition(":")
    try:
        return getattr(importlib.import_module(module), attribute)
    except ImportError as missing:
        raise BenchError(E_UNAVAILABLE, f"The adapter {name!r} needs a package that is not "
                         f"installed ({missing.name}). Install the extra it belongs to.") from None
