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


async def test_imbu_signs_through_the_throttled_signer_and_the_census_counts_it(tmp_path):
    """this.i 2p26rn9h: an external signer at 20 ms per operation, capped at 20 starts a
    second; every signature imbu makes goes through it, and the report counts and checks them."""
    path = tmp_path / "scenario.toml"
    path.write_text(scenario(stages='["issue", "deliver", "present", "verify"]', rate="1.0",
                             duration="3.0", signer="external", latency="20.0",
                             signer_rate="20.0"))
    report = await runner.run_once(load_scenario(path), tmp_path / "out", rate=1.0,
                                   repetition=1, harness_commit="test")
    _, events = read_timeline(tmp_path / "out" / "rate-1" / "rep-1" / "timeline.jsonl")
    assert [e.attrs for e in events if e.name == "stage_failed"] == []
    census = report["census"]
    home, = census["issuer"]["signers"]
    # Whether the 5% check passes depends on how busy the host is; that it judges the medians
    # it reports does not.
    lat, rate = home["latency"], home["rate"]
    assert lat["ok"] == (abs(lat["median_ms"] - 20.0) <= 1.0)
    assert not rate["exercised"] or rate["ok"] == (abs(rate["median_spacing_ms"] - 50.0) <= 2.5)
    assert report["refused"] is not census["ok"]
    assert home["latency"]["configured_ms"] == 20.0
    assert home["counts"]["incept"] == 1
    # Every answer imbu gave the adapter is one signature; the inception event is one more; and
    # each credential costs two, hand-traced on imbu ccc8f6c and unchanged at 8512c1d: the ixn
    # anchoring its registry's inception and its issuance (coalesced, heti @69ln7vtw), and the
    # grant. A change here is a change in what an HSM is asked to do per credential (test
    # plan, Part B #11).
    assert home["counts"]["sign"] == home["signed_responses"] + 1 + home["other_signatures"]
    assert home["per_issuance"] == 2.0


BULK_ISSUER = """[roles.issuer]
adapter = "imbu.bulk_issuer"
[roles.issuer.knobs]
knob_schema = 1
toad = 2
poll_ms = 100.0
operation_timeout_s = 120.0
homes = 1
clients = 2
copies = 1
pool = 8
"""


def bulk_scenario(*, duration="2.0", faults="", pool=8):
    text = scenario(stages='["issue"]', rate="1.0", duration=duration, faults=faults)
    start = text.index("[roles.issuer]")
    end = text.index("[roles.holder]")
    return text[:start] + BULK_ISSUER.replace("pool = 8", f"pool = {pool}") + "\n" + text[end:]


async def bulk_run(tmp_path, text):
    path = tmp_path / "scenario.toml"
    path.write_text(text)
    report = await runner.run_once(load_scenario(path), tmp_path / "out", rate=1.0,
                                   repetition=1, harness_commit="test")
    _, events = read_timeline(tmp_path / "out" / "rate-1" / "rep-1" / "timeline.jsonl")
    return report, events


async def test_the_sedi_bulk_ceremony_issues_a_pair_per_copy_and_the_management_pair(tmp_path):
    """this.i 4f987y88: each request is one ceremony; each issues 2 (copies + 1) credentials."""
    report, events = await bulk_run(tmp_path, bulk_scenario())
    assert [e.attrs for e in events if e.name == "stage_failed"] == []
    published = [e for e in events if e.name == "published"]
    assert len(published) == 2
    assert {e.attrs["credentials"] for e in published} == {4}
    assert {e.attrs["client"] for e in published} == {0, 1}
    assert report["intervals"]["requested->published"]["measured"] is True


async def test_a_crashed_bulk_home_reopens_and_the_ceremony_goes_on(tmp_path):
    """this.i 5ed29j5w: the restarted home reads its Utah agent credential back from disk."""
    # A ceremony the crash interrupted may be refused when imbu resumes it; new ones go on.
    _, events = await bulk_run(tmp_path, bulk_scenario(duration="12.0", pool=60, faults=(
        '[[faults]]\nkind = "crash"\ntarget = "issuer.h0"\nat_s = 1.5\n')))
    restarted = [e for e in events if e.name == "process_ready" and e.attrs["restart"]]
    published = [e for e in events if e.name == "published"]
    assert restarted and published
    assert max(e.t for e in published) > restarted[0].t
