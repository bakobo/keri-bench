"""The KERI-006 smoke run on the imbu stack, and the real adapters' contract run: the imbu issuer
adapter against imbu's asynchronous operations. Skipped, with the reason, until imbu has them."""

import inspect
from pathlib import Path

import pytest

from keri_bench.contract import contract
from keri_bench.scenario import load_scenario

SCENARIO = Path(__file__).parent.parent.parent / "scenarios" / "imbu-smoke.toml"


def imbu_has_operations() -> bool:
    from imbu.control import routes
    return "/v1/imbu/operations" in inspect.getsource(routes)


@pytest.mark.skipif(not imbu_has_operations(), reason=(
    "imbu's control routes are still synchronous: plan item imbu-async has not landed, and the "
    "imbu issuer adapter has no synchronous fallback (bench-harness brief)"))
async def test_the_imbu_stack_meets_the_adapter_contract_at_one_home(tmp_path):
    result = await contract(load_scenario(SCENARIO), tmp_path, harness_commit="test")
    assert result["ok"], result["problems"]
