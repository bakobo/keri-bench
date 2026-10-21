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


async def test_two_homes_with_two_clients_each_issue_to_one_holder(tmp_path):
    """this.i 2lkqz9mq: N homes share the deployment, and each credential names its home."""
    path = tmp_path / "scenario.toml"
    path.write_text(scenario(stages='["issue", "deliver", "present", "verify"]', rate="2.0",
                             duration="2.0", homes=2, clients=2))
    report = await runner.run_once(load_scenario(path), tmp_path / "out", rate=2.0,
                                   repetition=1, harness_commit="test")
    _, events = read_timeline(tmp_path / "out" / "rate-2" / "rep-1" / "timeline.jsonl")
    assert [e.attrs for e in events if e.name == "stage_failed"] == []
    homes = {(e.attrs["home"], e.attrs["client"]) for e in events if e.name == "accepted"}
    assert homes == {(0, 0), (1, 0), (0, 1), (1, 1)}
    assert len({c for e in events if e.name == "verified" for c in e.credentials}) == 4
    ready = {e.attrs["process"] for e in events if e.name == "process_ready"}
    assert {"issuer.h0", "issuer.h1", "witness_pool.w0", "witness_pool.w1"} <= ready
    assert report["refused"] is False


async def test_every_role_crashed_once_restarts_from_disk_and_issues_again(tmp_path):
    """this.i 5ed29j5w: each crash restarts its process on what it had on disk, and
    credentials requested after the last restart complete."""
    faults = "".join(f'[[faults]]\nkind = "crash"\ntarget = "{target}"\nat_s = {at}\n'
                     for target, at in (("issuer", 1.0), ("witness_pool.w0", 4.0),
                                        ("holder", 7.0), ("verifier", 10.0),
                                        ("state_distribution", 13.0)))
    path = tmp_path / "scenario.toml"
    path.write_text(scenario(stages='["issue", "deliver", "present", "verify"]', rate="1.0",
                             duration="24.0", faults=faults, count=3))
    report = await runner.run_once(load_scenario(path), tmp_path / "out", rate=1.0,
                                   repetition=1, harness_commit="test")
    _, events = read_timeline(tmp_path / "out" / "rate-1" / "rep-1" / "timeline.jsonl")
    assert [e for e in events if e.name == "fault_failed"] == []
    assert len([e for e in events if e.name == "fault_ended"]) == 5
    restarted = {e.attrs["process"] for e in events
                 if e.name == "process_ready" and e.attrs["restart"]}
    assert restarted == {"issuer.h0", "witness_pool.w0", "holder", "verifier",
                         "state_distribution.registrar", "state_distribution.observer"}
    assert all(f["time_to_ready_ns"] is not None for f in report["faults"])
    assert all(f["time_to_first_success_ns"] is not None for f in report["faults"])
    last = max(e.t for e in events if e.name == "fault_ended")
    later = {c for e in events if e.name == "requested" and e.t > last + 3_000_000_000
             for c in e.credentials}
    verified = {c for e in events if e.name == "verified" for c in e.credentials}
    assert later and later <= verified
