"""The imbu stack end to end: real witnesses, Registrar, Observer, imbu, holder and verifier."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from stack import scenario  # noqa: E402

from keri_bench import run as runner  # noqa: E402
from keri_bench.scenario import load_scenario  # noqa: E402
from keri_bench.timeline import read_timeline  # noqa: E402


async def test_real_credentials_flow_through_holder_verifier_and_revocation(tmp_path):
    path = tmp_path / "scenario.toml"
    # Several credentials, each in a registry of its own (imbu @2tdzyh85), so each verifies
    # and is revoked independently of the others; on a shared registry only the newest could.
    path.write_text(scenario(rate="1.0", duration="3.0"))
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
    assert summary["total"] >= 2
    assert summary["done"] + summary["resumed"] == summary["total"]
    verified = {c for e in events if e.name == "verified" for c in e.credentials}
    recognised = {c for e in events if e.name == "revocation_recognised" for c in e.credentials}
    assert len(verified) == len(recognised) == summary["total"]
    (tmp_path / "report.json").write_text(json.dumps(report))
