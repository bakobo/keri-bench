"""The imbu stack end to end: real witnesses, Registrar, Observer, imbu, holder and verifier."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from stack import scenario  # noqa: E402

from keri_bench import run as runner  # noqa: E402
from keri_bench.adapters import registry  # noqa: E402
from keri_bench.scenario import load_scenario  # noqa: E402
from keri_bench.timeline import read_timeline  # noqa: E402


@pytest.fixture
def sync_issuer(monkeypatch):
    monkeypatch.setitem(registry.ADAPTERS, "test.sync-imbu.issuer",
                        "sync_issuer:SyncImbuIssuer")


async def test_real_credentials_flow_through_holder_verifier_and_revocation(tmp_path,
                                                                            sync_issuer):
    path = tmp_path / "scenario.toml"
    path.write_text(scenario())
    report = await runner.run_once(load_scenario(path), tmp_path / "out", rate=1.0,
                                   repetition=1, harness_commit="test")
    run_dir = tmp_path / "out" / "rate-1" / "rep-1"
    _, events = read_timeline(run_dir / "timeline.jsonl")
    failed = [e.attrs for e in events if e.name == "stage_failed"]
    assert failed == []
    names = {e.name for e in events}
    assert {"delivered", "stored", "proof_generated", "verified", "revocation_recognised",
            "sync_full_started", "sync_full_done"} <= names
    summary = report["reconciliation"]["summary"]
    assert summary["done"] + summary["resumed"] == summary["total"] == 3
    (tmp_path / "report.json").write_text(json.dumps(report))
