"""The run orchestrator: planning refusals, a whole run, faults, and the sweep summary."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from scenarios import scenario  # noqa: E402

from keri_bench import run as runner  # noqa: E402
from keri_bench.adapters import registry  # noqa: E402
from keri_bench.errors import BenchError  # noqa: E402
from keri_bench.scenario import load_scenario  # noqa: E402
from keri_bench.timeline import read_timeline  # noqa: E402


def load(tmp_path, text):
    path = tmp_path / "scenario.toml"
    path.write_text(text)
    return load_scenario(path)


def test_planning_refuses_stages_whose_roles_are_not_configured(tmp_path):
    text = scenario(names=("witness_pool", "state_distribution", "issuer"))
    with pytest.raises(BenchError) as refused:
        runner.plan(load(tmp_path, text))
    assert refused.value.code == "e.input.missing.scenario.role.f"


def test_planning_refuses_an_adapter_configured_for_another_role(tmp_path):
    text = scenario(stages='["issue"]', names=("state_distribution", "issuer")).replace(
        'adapter = "synthetic.state_distribution"', 'adapter = "synthetic.holder"')
    with pytest.raises(BenchError) as refused:
        runner.plan(load(tmp_path, text))
    assert refused.value.code == "e.input.range.scenario.adapter.role.f"


def test_planning_refuses_two_sources_for_one_milestone(tmp_path, monkeypatch):
    from keri_bench.adapters.base import Declaration, Observation
    from keri_bench.synthetic.adapters import SyntheticIssuer

    class Greedy(SyntheticIssuer):
        declaration = Declaration(
            adapter="greedy", role=SyntheticIssuer.declaration.role, version="1",
            milestones={**SyntheticIssuer.declaration.milestones,
                        "requested": Observation("observed", "harness")},
            knobs=SyntheticIssuer.declaration.knobs, tested_against={})

    monkeypatch.setitem(registry.ADAPTERS, "test.greedy", "test_run:Greedy")
    monkeypatch.setattr(sys.modules[__name__], "Greedy", Greedy, raising=False)
    text = scenario(stages='["issue"]', names=("state_distribution", "issuer")).replace(
        'adapter = "synthetic.issuer"', 'adapter = "test.greedy"')
    with pytest.raises(BenchError) as refused:
        runner.plan(load(tmp_path, text))
    assert refused.value.code == "e.state.conflict.run.milestone-source.f"


def test_planning_refuses_a_crash_of_an_unconfigured_role(tmp_path):
    text = scenario(stages='["issue"]', names=("state_distribution", "issuer"),
                    faults='[[faults]]\nkind = "crash"\ntarget = "holder"\nat_s = 0.1\n')
    with pytest.raises(BenchError) as refused:
        runner.plan(load(tmp_path, text))
    assert refused.value.code == "e.input.range.scenario.fault.target.f"


def test_a_crash_may_name_one_process_of_a_configured_role(tmp_path):
    names = ("state_distribution", "issuer")
    ok = scenario(stages='["issue"]', names=names,
                  faults='[[faults]]\nkind = "crash"\ntarget = "issuer.w0"\nat_s = 0.1\n')
    assert runner.plan(load(tmp_path, ok)).scenario.faults[0].target == "issuer.w0"
    bad = ok.replace('"issuer.w0"', '"holder.w0"')
    with pytest.raises(BenchError) as refused:
        runner.plan(load(tmp_path, bad))
    assert refused.value.code == "e.input.range.scenario.fault.target.f"


def test_the_registry_refuses_unknown_and_uninstalled_adapters(monkeypatch):
    with pytest.raises(BenchError) as unknown:
        registry.lookup("nobody.issuer")
    assert unknown.value.code == "e.input.range.scenario.adapter.f"
    monkeypatch.setitem(registry.ADAPTERS, "ghost.issuer", "no_such_module_xyz:Issuer")
    with pytest.raises(BenchError) as missing:
        registry.lookup("ghost.issuer")
    assert missing.value.code == "e.feature.unsupported.adapter.f"


async def test_a_whole_lifecycle_run_writes_a_timeline_provenance_and_report(tmp_path):
    plan = load(tmp_path, scenario())
    report = await runner.run_once(plan, tmp_path / "out", rate=10.0, repetition=1,
                                   harness_commit="test")
    run_dir = tmp_path / "out" / "rate-10" / "rep-1"
    for name in ("timeline.jsonl", "provenance.json", "report.json", "report.md"):
        assert (run_dir / name).exists(), name
    header, events = read_timeline(run_dir / "timeline.jsonl")
    names = {e.name for e in events}
    assert {"run_started", "run_ended", "clock_offset", "requested", "dispatched", "accepted",
            "constructed", "witnessed", "published", "grant_sent", "delivered", "stored",
            "proof_generated", "verified", "revocation_requested", "revocation_published",
            "revocation_recognised", "process_ready"} <= names
    assert "stage_failed" not in names
    assert report["reconciliation"]["summary"]["done"] == 10
    assert report["observability"]["pairs"]["revocation_vs_recognition"]["measurable"]
    provenance = json.loads((run_dir / "provenance.json").read_text())
    assert provenance["run"]["rate"] == 10.0
    assert provenance["components"]["issuer"] == {"synthetic": "1"}
    assert set(provenance["measurement_cost"]["links"]) == {
        "state_distribution.api", "issuer.api", "holder.api", "verifier.api"}
    assert "harness.driver" in provenance["adapters"]


async def test_faults_are_scheduled_recorded_and_survived(tmp_path):
    faults = "\n".join([
        '[[faults]]\nkind = "crash"\ntarget = "issuer"\nat_s = 0.3\nduration_s = 0.1\n',
        '[[faults]]\nkind = "partition"\ntarget = "holder.api"\nat_s = 0.2\nduration_s = 0.3\n',
        '[[faults]]\nkind = "delay"\ntarget = "verifier.api"\nat_s = 0.1\nduration_s = 0.5\n'
        'delay_ms = 20.0\n',
        '[[faults]]\nkind = "duplicate"\ntarget = "state_distribution.api"\nat_s = 0.0\n'
        'duration_s = 1.0\n',
    ])
    plan = load(tmp_path, scenario(stages='["issue", "deliver", "present", "verify"]',
                                   faults=faults))
    report = await runner.run_once(plan, tmp_path / "out", rate=10.0, repetition=1,
                                   harness_commit="test")
    _, events = read_timeline(tmp_path / "out" / "rate-10" / "rep-1" / "timeline.jsonl")
    started = sorted(e.attrs["kind"] for e in events if e.name == "fault_started")
    assert started == ["crash", "delay", "duplicate", "partition"]
    assert len([e for e in events if e.name == "fault_ended"]) == 4
    assert any(e.name == "process_killed" and e.attrs["process"] == "issuer" for e in events)
    assert any(e.name == "process_ready" and e.attrs["restart"] for e in events)
    assert any(e.name == "stage_failed" for e in events)
    assert len(report["faults"]) == 4
    summary = report["reconciliation"]["summary"]
    assert summary["done"] + summary["resumed"] + summary["lost"] == summary["total"] == 10
    assert summary["lost"] >= 1 and summary["resumed"] >= 1  # the crash cost credentials


async def test_a_fault_on_a_link_the_run_does_not_have_refuses_before_driving(tmp_path):
    plan = load(tmp_path, scenario(stages='["issue"]', names=("state_distribution", "issuer"),
                                   faults='[[faults]]\nkind = "delay"\ntarget = "nowhere.api"\n'
                                          'at_s = 0.1\nduration_s = 0.1\ndelay_ms = 1.0\n'))
    with pytest.raises(BenchError) as refused:
        await runner.run_once(plan, tmp_path / "out", rate=10.0, repetition=1,
                              harness_commit="test")
    assert refused.value.code == "e.input.range.scenario.fault.target.f"
    _, events = read_timeline(tmp_path / "out" / "rate-10" / "rep-1" / "timeline.jsonl")
    assert "requested" not in {e.name for e in events}


async def test_a_run_that_never_verifies_does_not_ask_the_verifier(tmp_path, fakes,
                                                                   monkeypatch):
    from fake_adapters import FakeVerifier
    asked = []

    async def status(self, credential):
        asked.append(credential)
        return {"status": "valid", "said": None}

    monkeypatch.setattr(FakeVerifier, "status", status)
    plan = load(tmp_path, fake_scenario(0.0, verifier=True))  # issue and deliver only
    report = await runner.run_once(plan, tmp_path / "out", rate=20.0, repetition=1,
                                   harness_commit="test")
    assert asked == [] and report["reconciliation"] == "not measured"


async def test_an_issue_only_run_without_verifier_or_witness_pool_skips_reconciliation(tmp_path):
    plan = load(tmp_path, scenario(stages='["issue"]', names=("state_distribution", "issuer")))
    report = await runner.run_once(plan, tmp_path / "out", rate=10.0, repetition=1,
                                   harness_commit="test")
    assert report["reconciliation"] == "not measured"


def fake_report(rate, rep, achieved, p99, refused=False):
    return {"run": {"repetition": rep}, "refused": refused,
            "refused_reason": "lagged" if refused else None,
            "throughput": {"milestones": {"constructed": {"per_s": achieved}}},
            "incomplete": {"latency": {"p99": {"value": p99, "lower_bound": False}}}}


def test_the_sweep_summary_finds_saturation_and_lists_refused_runs(tmp_path):
    plan = load(tmp_path, scenario(stages='["issue"]', rates="[10.0, 20.0, 30.0]",
                                   names=("state_distribution", "issuer")))
    reports = {
        10.0: [fake_report(10, r, 10.0, 100) for r in (1, 2, 3)],
        20.0: [fake_report(20, r, 20.0, 110) for r in (1, 2)] + [fake_report(20, 3, 0, 0, True)],
        30.0: [fake_report(30, r, 20.1, 900) for r in (1, 2, 3)],
    }
    summary = runner.summarize(plan, reports)
    assert summary["saturation"] == {"offered": 30.0, "reason": "throughput", "achieved": 20.1}
    assert summary["refused"] == [{"rate": 20.0, "repetition": 3, "reason": "lagged"}]
    assert summary["steps"][1]["runs"] == 2
    assert runner.summarize(plan, {10.0: [fake_report(10, 1, 10.0, 1)]})["saturation"] is None


def test_a_summary_with_no_accepted_runs_or_no_latency_has_no_points(tmp_path):
    plan = load(tmp_path, scenario(stages='["issue"]', rates="[10.0, 20.0]",
                                   names=("state_distribution", "issuer")))
    unmeasured = fake_report(10, 1, 10.0, 0)
    unmeasured["incomplete"]["latency"] = {"measured": False}
    summary = runner.summarize(plan, {10.0: [unmeasured], 20.0: [fake_report(20, 1, 0, 0, True)]})
    assert summary["steps"][0]["p99_ns"] is None and summary["steps"][1]["achieved"] is None
    assert summary["saturation"] is None


def test_a_sweep_that_never_saturates_says_so(tmp_path):
    plan = load(tmp_path, scenario(stages='["issue"]', rates="[10.0, 20.0]",
                                   names=("state_distribution", "issuer")))
    summary = runner.summarize(plan, {10.0: [fake_report(10, 1, 10.0, 100)],
                                      20.0: [fake_report(20, 1, 20.0, 100)]})
    assert summary["saturation"] is None


async def test_a_sweep_runs_more_repetitions_until_enough_are_accepted(tmp_path, monkeypatch):
    plan = load(tmp_path, scenario(stages='["issue"]', rates="[10.0, 20.0]",
                                   names=("state_distribution", "issuer")))
    calls = []

    async def fake_run_once(scenario, out, *, rate, repetition, harness_commit, calibration):
        calls.append((rate, repetition))
        # At 10, the first two are refused; at 20, every run is.
        refused = rate == 20.0 or repetition <= 2
        return fake_report(rate, repetition, rate, 100, refused=refused)

    monkeypatch.setattr(runner, "run_once", fake_run_once)
    summary = await runner.sweep(plan, tmp_path / "out", harness_commit="t", attempts=6)
    assert calls == [(10.0, r) for r in range(1, 6)] + [(20.0, r) for r in range(1, 7)]
    assert summary["steps"][0]["runs"] == 3 and summary["steps"][1]["runs"] == 0
    assert summary["attempts"] == 6
    calls.clear()
    await runner.sweep(plan, tmp_path / "again", harness_commit="t")
    assert len(calls) == 6  # by default, exactly the scenario's repetitions per step


async def test_a_sweep_refuses_fewer_attempts_than_repetitions(tmp_path):
    plan = load(tmp_path, scenario(stages='["issue"]', names=("state_distribution", "issuer")))
    with pytest.raises(BenchError) as refused:
        await runner.sweep(plan, tmp_path / "out", harness_commit="t", attempts=2)
    assert refused.value.code == "e.input.range.sweep.attempts.f"


async def test_a_sweep_writes_its_summary(tmp_path):
    plan = load(tmp_path, scenario(stages='["issue"]', names=("state_distribution", "issuer"),
                                   duration="0.5"))
    summary = await runner.sweep(plan, tmp_path / "out", harness_commit="test")
    assert json.loads((tmp_path / "out" / "sweep.json").read_text()) == summary
    assert summary["steps"][0]["runs"] + len(summary["refused"]) == 3


FAKES = {"fake.issuer": "fake_adapters:FakeIssuer", "fake.holder": "fake_adapters:FakeHolder",
         "fake.verifier": "fake_adapters:FakeVerifier"}


def fake_scenario(issue_s, drain="10.0", stages='["issue", "deliver"]', verifier=False):
    text = f"""scenario_schema = 1
name = "fake"
stages = {stages}
[load]
rates = [20.0]
duration_s = 0.3
warmup_s = 0.0
drain_s = {drain}
repetitions = 3
measure = ["requested", "constructed"]
[roles.issuer]
adapter = "fake.issuer"
[roles.issuer.knobs]
knob_schema = 1
issue_s = {issue_s}
[roles.holder]
adapter = "fake.holder"
[roles.holder.knobs]
knob_schema = 1
"""
    if verifier:
        text += '[roles.verifier]\nadapter = "fake.verifier"\n[roles.verifier.knobs]\n' \
                'knob_schema = 1\n'
    return text


@pytest.fixture
def fakes(monkeypatch):
    for name, where in FAKES.items():
        monkeypatch.setitem(registry.ADAPTERS, name, where)


async def test_a_stack_that_delivers_itself_is_not_relayed(tmp_path, fakes):
    plan = load(tmp_path, fake_scenario(0.0))
    assert "harness.relay" not in [d.adapter for d in runner.plan(plan).harness]
    report = await runner.run_once(plan, tmp_path / "out", rate=20.0, repetition=1,
                                   harness_commit="test")
    _, events = read_timeline(tmp_path / "out" / "rate-20" / "rep-1" / "timeline.jsonl")
    sent = [e for e in events if e.name == "grant_sent"]
    assert sent and all(e.attrs["adapter"] == "fake.issuer" for e in sent)
    assert report["reconciliation"] == "not measured"


async def test_lifecycles_still_running_after_the_drain_are_cancelled(tmp_path, fakes):
    plan = load(tmp_path, fake_scenario(30.0, drain="0.05"))
    report = await runner.run_once(plan, tmp_path / "out", rate=20.0, repetition=1,
                                   harness_commit="test")
    assert report["incomplete"]["count"] == 6


async def test_reconciliation_retries_a_failed_question_and_asks_a_few_at_a_time(
        tmp_path, fakes, monkeypatch):
    from fake_adapters import FakeVerifier
    tries, peak, active = {}, [0], [0]

    async def flaky(self, credential):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        await asyncio.sleep(0.01)
        active[0] -= 1
        tries[credential] = tries.get(credential, 0) + 1
        if tries[credential] < 2:
            raise ConnectionError("busy")
        return {"status": "valid", "said": f"E{credential}"}

    monkeypatch.setattr(FakeVerifier, "status", flaky)
    monkeypatch.setattr(runner, "RECONCILE_PAUSE_S", 0.0)
    plan = load(tmp_path, fake_scenario(0.0, stages='["issue", "deliver", "verify"]',
                                        verifier=True))
    report = await runner.run_once(plan, tmp_path / "out", rate=20.0, repetition=1,
                                   harness_commit="test")
    assert report["reconciliation"]["summary"]["done"] == 6
    assert set(tries.values()) == {2}
    assert peak[0] <= runner.RECONCILE_CONCURRENCY


async def test_a_verifier_that_fails_to_answer_reconciles_as_unknown(tmp_path, fakes):
    plan = load(tmp_path, fake_scenario(0.0, stages='["issue", "deliver", "verify"]',
                                        verifier=True))
    report = await runner.run_once(plan, tmp_path / "out", rate=20.0, repetition=1,
                                   harness_commit="test")
    # Asked and asked again, it never answered: unanswered, not lost (the verifier said nothing).
    assert report["reconciliation"]["summary"]["unanswered"] == 6
    assert report["reconciliation"]["summary"]["lost"] == 0


async def test_announced_endpoints_are_fronted_unless_already_fronted_or_not_urls(tmp_path,
                                                                                    monkeypatch):
    monkeypatch.setitem(registry.ADAPTERS, "fake.witness_pool", "fake_adapters:FakeWitnessPool")
    from keri_bench.adapters.base import Declaration  # noqa: F401
    text = fake_scenario(0.0).replace("[roles.holder]", '[roles.witness_pool]\nadapter = '
                                      '"fake.witness_pool"\n[roles.witness_pool.knobs]\n'
                                      'knob_schema = 1\n[roles.holder]')
    for name, where in FAKES.items():
        monkeypatch.setitem(registry.ADAPTERS, name, where)
    run = runner.Run(runner.plan(load(tmp_path, text)), tmp_path / "out", 1.0, 1, "test")
    run.harness_clock, run.realtime_clock = "monotonic@b", "realtime@h"
    run.sink, run.procs = (lambda event: None), None
    await run._bring_up()
    try:
        peers = run.peers[runner.Role.WITNESS_POOL]
        fronted = run.links["witness_pool.advertised"].url
        assert peers["oobis"][0] == f"{fronted}/oobi/B1/controller"
        second = run.links["witness_pool.oobis.1"].url
        assert peers["oobis"][1] == f"{second}/oobi/B2"
        assert peers["aid"] == "Bwitness"
        assert peers["single"] == run.links["witness_pool.single"].url + "/x"
    finally:
        for link in run.links.values():
            await link.stop()


def scripted(accept, answers, stable=2, stages='["issue", "verify", "revoke"]'):
    return f"""scenario_schema = 1
name = "scripted"
stages = {stages}
[recognition]
poll_ms = 5.0
stable_polls = {stable}
[load]
rates = [10.0]
duration_s = 0.1
warmup_s = 0.0
drain_s = 5.0
repetitions = 3
measure = ["requested", "constructed"]
[roles.issuer]
adapter = "fake.revoking_issuer"
[roles.issuer.knobs]
knob_schema = 1
issue_s = 0.0
[roles.verifier]
adapter = "fake.scripted_verifier"
[roles.verifier.knobs]
knob_schema = 1
accept = {"true" if accept else "false"}
answers = "{answers}"
"""


@pytest.fixture
def scripted_fakes(monkeypatch):
    monkeypatch.setitem(registry.ADAPTERS, "fake.revoking_issuer",
                        "fake_adapters:FakeRevokingIssuer")
    monkeypatch.setitem(registry.ADAPTERS, "fake.scripted_verifier",
                        "fake_adapters:ScriptedVerifier")


async def scripted_run(tmp_path, text):
    await runner.run_once(load(tmp_path, text), tmp_path / "out", rate=10.0, repetition=1,
                          harness_commit="test")
    return read_timeline(tmp_path / "out" / "rate-10" / "rep-1" / "timeline.jsonl")[1]


async def test_a_revocation_a_later_poll_disproves_is_not_recognised(tmp_path, scripted_fakes):
    events = await scripted_run(tmp_path, scripted(True, "rrv"))
    assert "revocation_recognised" not in {e.name for e in events}
    assert "stage_failed" not in {e.name for e in events}


async def test_a_revocation_is_recognised_at_the_start_of_the_final_streak(tmp_path,
                                                                          scripted_fakes):
    events = await scripted_run(tmp_path, scripted(True, "rvr"))
    recognised = [e for e in events if e.name == "revocation_recognised"]
    assert len(recognised) == 1
    assert recognised[0].attrs["relapsed"] is True
    assert recognised[0].attrs["first_seen"] < recognised[0].t
    assert recognised[0].attrs["polls"] >= 3


async def test_a_verifier_that_refuses_the_presentation_fails_the_stage(tmp_path,
                                                                        scripted_fakes):
    events = await scripted_run(tmp_path, scripted(False, "r"))
    failed = [e for e in events if e.name == "stage_failed"]
    assert failed and failed[0].attrs["code"] == "e.proof.presentation.f"
    assert failed[0].attrs["stage"] == "verify"


async def test_a_fault_that_cannot_be_applied_refuses_the_run(tmp_path, fakes, monkeypatch):
    async def broken(self, fault, window_start):
        raise RuntimeError("the proxy would not take it")

    monkeypatch.setattr(runner.Run, "_fault", broken)
    text = fake_scenario(0.0) + ('[[faults]]\nkind = "crash"\ntarget = "issuer"\nat_s = 0.0\n')
    report = await runner.run_once(load(tmp_path, text), tmp_path / "out", rate=20.0,
                                   repetition=1, harness_commit="test")
    assert report["refused"] is True
    assert "the proxy would not take it" in report["failures"]["fault_failed"]



async def test_a_confirmation_poll_that_fails_withholds_recognition(tmp_path, scripted_fakes,
                                                                   monkeypatch):
    from fake_adapters import ScriptedVerifier

    original = ScriptedVerifier.status

    async def flaky(self, credential):
        polls = self.ctx.shared.get("polls", {}).get(credential, 0)
        if polls >= 2:
            raise ConnectionResetError("the verifier went away")
        return await original(self, credential)

    monkeypatch.setattr(ScriptedVerifier, "status", flaky)
    events = await scripted_run(tmp_path, scripted(True, "r"))
    assert "revocation_recognised" not in {e.name for e in events}


async def test_a_crash_of_a_role_with_no_process_is_a_failed_fault(tmp_path, fakes):
    text = fake_scenario(0.0) + ('[[faults]]\nkind = "crash"\ntarget = "issuer"\nat_s = 0.0\n')
    report = await runner.run_once(load(tmp_path, text), tmp_path / "out", rate=20.0,
                                   repetition=1, harness_commit="test")
    assert "no running process" in report["failures"]["fault_failed"]


async def test_a_failing_first_poll_fails_the_revoke_stage(tmp_path, scripted_fakes, monkeypatch):
    from fake_adapters import ScriptedVerifier

    async def down(self, credential):
        raise ConnectionResetError("the verifier went away")

    monkeypatch.setattr(ScriptedVerifier, "status", down)
    events = await scripted_run(tmp_path, scripted(True, "r"))
    assert [e.attrs["stage"] for e in events if e.name == "stage_failed"] == ["revoke"]


async def test_a_malformed_confirmation_answer_withholds_recognition(tmp_path, scripted_fakes,
                                                                    monkeypatch):
    from fake_adapters import ScriptedVerifier

    original = ScriptedVerifier.status

    async def odd(self, credential):
        answer = await original(self, credential)
        return [] if self.ctx.shared["polls"][credential] > 2 else answer

    monkeypatch.setattr(ScriptedVerifier, "status", odd)
    events = await scripted_run(tmp_path, scripted(True, "r"))
    assert "revocation_recognised" not in {e.name for e in events}


async def test_a_crash_of_a_role_whose_process_already_exited_is_a_failed_fault(tmp_path):
    from types import SimpleNamespace

    from keri_bench.scenario import Fault
    run = runner.Run(runner.plan(load(tmp_path, scenario(stages='["issue"]', names=(
        "state_distribution", "issuer")))), tmp_path / "out", 1.0, 1, "test")
    dead = SimpleNamespace(proc=SimpleNamespace(returncode=1))
    run.procs = SimpleNamespace(names=lambda: ["issuer"], process=lambda name: dead)
    run.harness_clock = "monotonic@b"
    run.sink = lambda event: None
    with pytest.raises(BenchError) as refused:
        await run._fault(Fault("crash", "issuer", 0.0), run.clock())
    assert "no running process" in refused.value.message


async def test_a_revoked_answer_about_another_credential_is_not_evidence(tmp_path):
    from types import SimpleNamespace
    run = runner.Run(runner.plan(load(tmp_path, scenario(stages='["issue"]', names=(
        "state_distribution", "issuer")))), tmp_path / "out", 1.0, 1, "test")

    async def status(credential):
        return {"status": "revoked", "said": "Eother"}

    run.adapters[runner.Role.VERIFIER] = SimpleNamespace(status=status)
    run.shared["said"] = {"c1": "Emine"}
    assert (await run._status("c1"))[1] is False
    run.shared["said"] = {"c1": "Eother"}
    assert (await run._status("c1"))[1] is True


async def test_a_crash_restarts_every_process_of_the_role_even_one_already_down(tmp_path):
    from types import SimpleNamespace

    from keri_bench.scenario import Fault
    run = runner.Run(runner.plan(load(tmp_path, scenario(stages='["issue"]', names=(
        "state_distribution", "issuer")))), tmp_path / "out", 1.0, 1, "test")
    states = {"issuer": None, "issuer.helper": 1}
    killed, restarted = [], []

    async def kill(name):
        killed.append(name)

    async def restart(name):
        restarted.append(name)

    run.procs = SimpleNamespace(
        names=lambda: list(states), kill=kill, restart=restart,
        process=lambda name: SimpleNamespace(proc=SimpleNamespace(returncode=states[name])))
    run.harness_clock, run.sink = "monotonic@b", (lambda event: None)
    await run._fault(Fault("crash", "issuer", 0.0), run.clock())
    assert killed == ["issuer"] and restarted == ["issuer", "issuer.helper"]


# --- preseed, soak stop and store size (this.i 9yx6cg92, 7f9jfc99) ---------------------------

def soak_scenario(*, rate="20.0", duration="0.3", stop=None, preseed=None, soak=True):
    text = fake_scenario(0.0).replace("rates = [20.0]", f"rates = [{rate}]").replace(
        "duration_s = 0.3", f"duration_s = {duration}")
    if stop is not None:
        text += f"[stop]\ncredentials = {stop}\n"
    if preseed is not None:
        text += f"[preseed]\ncredentials = {preseed}\nconcurrency = 2\n"
    if soak:
        text += "[soak]\nbucket = 2\nper_credentials = 1000\nsample_s = 0.05\n"
    return text


async def soak_events(tmp_path, text, rate=20.0, shared=None, monkeypatch=None):
    if shared is not None:
        original = runner.Run.__init__

        def seeded(self, *args, **kw):
            original(self, *args, **kw)
            self.shared.update(shared)

        monkeypatch.setattr(runner.Run, "__init__", seeded)
    report = await runner.run_once(load(tmp_path, text), tmp_path / "out", rate=rate,
                                   repetition=1, harness_commit="test")
    _, events = read_timeline(tmp_path / "out" / f"rate-{rate:g}" / "rep-1" / "timeline.jsonl")
    return report, events


async def test_a_preseed_issues_through_the_stack_before_the_window_opens(tmp_path, fakes):
    report, events = await soak_events(tmp_path, soak_scenario(preseed=5))
    names = [e.name for e in events]
    assert names.index("preseed_ended") < names.index("run_started")
    started, = [e.attrs for e in events if e.name == "preseed_started"]
    ended, = [e.attrs for e in events if e.name == "preseed_ended"]
    assert started == {"requested": 5, "concurrency": 2}
    assert (ended["requested"], ended["failed"]) == (5, 0) and ended["elapsed_ns"] > 0
    seeded = {c for e in events if e.name == "constructed" for c in e.credentials
              if c.startswith("p")}
    assert seeded == {f"p{i:07d}" for i in range(5)}
    assert not any(c.startswith("p") for e in events if e.name == "requested"
                   for c in e.credentials)
    assert report["window"]["credentials"] == 6
    assert report["drift"]["preseeded"] == 5
    record = json.loads((tmp_path / "out" / "rate-20" / "rep-1" / "provenance.json")
                        .read_text())
    assert record["run"]["preseed"] == {"credentials": 5, "concurrency": 2}
    assert record["run"]["soak"] == {"bucket": 2, "per_credentials": 1000, "sample_s": 0.05,
                                     "stop_credentials": None}


async def test_a_failed_preseed_issuance_is_counted_not_fatal(tmp_path, fakes, monkeypatch):
    report, events = await soak_events(
        tmp_path, soak_scenario(preseed=3), shared={"refuse": {"p0000001"}},
        monkeypatch=monkeypatch)
    ended, = [e.attrs for e in events if e.name == "preseed_ended"]
    assert (ended["requested"], ended["failed"]) == (3, 1)
    assert ended["first_error"] == "ConnectionError: the issuer refused p0000001"
    assert report["drift"]["preseeded"] == 2


async def test_a_soak_stops_once_enough_credentials_complete(tmp_path, fakes):
    report, events = await soak_events(tmp_path, soak_scenario(duration="5.0", stop=4))
    stopped, = [e.attrs for e in events if e.name == "stop_reached"]
    assert stopped == {"reason": "credentials", "completed": 4}
    requested = sum(1 for e in events if e.name == "requested")
    assert 4 <= requested < 20  # 100 were scheduled over 5 s
    assert report["run"]["soak"]["stop_credentials"] == 4


async def test_a_soak_that_runs_out_of_time_says_so(tmp_path, fakes):
    _, events = await soak_events(tmp_path, soak_scenario(stop=1000))
    stopped, = [e.attrs for e in events if e.name == "stop_reached"]
    # Counted when the schedule ended, before the last request's lifecycle has had a turn.
    assert stopped["reason"] == "duration" and stopped["completed"] in (5, 6)


async def test_a_soak_samples_the_issuers_store_until_the_drain_ends(tmp_path, fakes):
    report, events = await soak_events(tmp_path, soak_scenario(duration="0.4"))
    sizes = [e.attrs["bytes"] for e in events if e.name == "footprint"]
    assert len(sizes) >= 3 and sizes == sorted(sizes) and sizes[0] == 0
    assert "stop_reached" not in {e.name for e in events}
    assert any(b["store_bytes"] for b in report["drift"]["buckets"])


async def test_an_issuer_that_cannot_size_its_store_writes_no_samples(tmp_path, fakes,
                                                                      monkeypatch):
    from fake_adapters import FakeIssuer
    from keri_bench.adapters.base import IssuerAdapter
    monkeypatch.setattr(FakeIssuer, "footprint", IssuerAdapter.footprint)
    _, events = await soak_events(tmp_path, soak_scenario())
    assert "footprint" not in {e.name for e in events}


async def test_a_run_without_a_soak_samples_nothing_and_has_no_soak_section(tmp_path, fakes):
    report, events = await soak_events(tmp_path, soak_scenario(soak=False))
    assert {"footprint", "stop_reached", "preseed_started"}.isdisjoint(e.name for e in events)
    assert "soak" not in report["run"] and "preseed" not in report["run"]
