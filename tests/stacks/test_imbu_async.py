"""The KERI-006 smoke run on the imbu stack, and the real adapters' contract run: the imbu issuer
adapter against imbu's asynchronous operations (imbu 670a3d8)."""

from pathlib import Path

from keri_bench.contract import contract
from keri_bench.scenario import load_scenario

SCENARIO = Path(__file__).parent.parent.parent / "scenarios" / "imbu-smoke.toml"


async def test_the_imbu_stack_meets_the_adapter_contract_at_one_home(tmp_path):
    result = await contract(load_scenario(SCENARIO), tmp_path, harness_commit="test")
    assert result["ok"], result["problems"]
