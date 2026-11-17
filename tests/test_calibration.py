"""The calibration (Part B #1): the comparison logic on fixed inputs, then the real thing."""

import json
import tarfile
from pathlib import Path

import pytest

from keri_bench import calibration
from keri_bench.errors import BenchError

SCENARIOS = Path(__file__).parent.parent / "scenarios"
# A calibration recorded on kelun, 2026-11-17, that passed the strict gate live (this.i
# 0hykugwr). Record it again with `keri-bench calibrate` when the timeline schema or the
# calibration scenarios change, keeping only scenarios/, timeline.jsonl, provenance.json and
# truth.jsonl.
RECORDING = Path(__file__).parent / "golden" / "calibration" / "kelun-2026-11-17.tar.gz"
MS = 1_000_000
REP = "intervals/rate-15/rep-1"


def recording(tmp_path) -> Path:
    path = tmp_path / "recording"
    with tarfile.open(RECORDING) as archive:
        archive.extractall(path, filter="data")
    return path


def rewrite_jsonl(path: Path, change) -> None:
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    path.write_text("".join(json.dumps(line) + "\n" for line in change(lines)))


def interval(count, p50, p99, resolution=1, measured=True):
    return {"measured": measured, "count": count, "p50": p50, "p99": p99, "max": p99,
            "resolution_ns": resolution}


def run_dir(tmp_path, name, truth, intervals, refused=False):
    path = tmp_path / name
    path.mkdir(parents=True)
    (path / "truth.jsonl").write_text("".join(
        json.dumps({"credential": f"c{i}", "start": start, "end": end, "injected_ns": v,
                    "elapsed_ns": v}) + "\n"
        for (start, end), values in truth.items() for i, v in enumerate(values)))
    (path / "report.json").write_text(json.dumps({"refused": refused, "intervals": intervals}))
    return path


def test_compare_holds_each_transition_to_the_largest_of_5_percent_its_resolution_or_5_ms(
        tmp_path):
    # Hand-computed truth: 100 values 10..1000 ms; p50 is the 50th (500 ms, 5% = 25 ms), p99 the
    # 99th (990 ms).
    values = [i * 10 * MS for i in range(1, 101)]
    path = run_dir(tmp_path, "r", {("a", "b"): values, ("c", "d"): values, ("e", "f"): values,
                                   ("g", "h"): values}, {
        "a->b": interval(100, 520 * MS, 990 * MS),                     # +20 ms <= 25 ms: ok
        "c->d": interval(100, 530 * MS, 990 * MS),                     # +30 ms > 25 ms: not ok
        "e->f": interval(100, 535 * MS, 990 * MS, resolution=40 * MS),  # 35 ms <= resolution
        "g->h": interval(0, 0, 0, measured=False)})
    result = calibration.compare(path)
    assert result["a->b"]["ok"] and result["e->f"]["ok"] and not result["c->d"]["ok"]
    assert result["a->b"]["p50"] == {"truth_ns": 500 * MS, "measured_ns": 520 * MS,
                                     "error_ns": 20 * MS, "tolerance_ns": 25 * MS}
    assert result["e->f"]["p50"]["tolerance_ns"] == 40 * MS
    assert result["g->h"] == {"measured": False}


def test_a_short_transition_is_held_to_5_ms_not_5_percent(tmp_path):
    # 1..100 ms: p50 50 ms, whose 5% is 2.5 ms, so the 5 ms floor is the tolerance.
    values = [i * MS for i in range(1, 101)]
    path = run_dir(tmp_path, "r", {("a", "b"): values, ("c", "d"): values}, {
        "a->b": interval(100, 50 * MS + 4_500_000, 99 * MS),   # +4.5 ms <= 5 ms: ok
        "c->d": interval(100, 50 * MS + 5_500_000, 99 * MS)})  # +5.5 ms > 5 ms: not ok
    result = calibration.compare(path)
    assert result["a->b"]["ok"] and not result["c->d"]["ok"]
    assert result["a->b"]["p50"]["tolerance_ns"] == calibration.INTERVAL_FLOOR_NS == 5 * MS


def test_compare_fails_a_transition_whose_sample_count_differs(tmp_path):
    path = run_dir(tmp_path, "r", {("a", "b"): [MS] * 10}, {"a->b": interval(9, MS, MS)})
    assert not calibration.compare(path)["a->b"]["ok"]


def test_compare_refuses_a_run_without_truth(tmp_path):
    with pytest.raises(BenchError) as refused:
        calibration.compare(tmp_path)
    assert refused.value.code == "e.state.missing.calibration.truth.f"


def test_the_interval_budget_keeps_the_worst_error_and_skips_refused_runs(tmp_path):
    values = [i * MS for i in range(1, 101)]
    run_dir(tmp_path, "rate-1/rep-1", {("a", "b"): values}, {"a->b": interval(100, 51 * MS,
                                                                              98 * MS)})
    run_dir(tmp_path, "rate-1/rep-2", {("a", "b"): values}, {"a->b": interval(100, 48 * MS,
                                                                              99 * MS)})
    run_dir(tmp_path, "rate-1/rep-3", {("a", "b"): values}, {}, refused=True)
    result = calibration._intervals(tmp_path)
    assert result["ok"]
    assert result["budget"] == {"a->b": {"p50_error_ns": -2 * MS, "p99_error_ns": -1 * MS}}
    assert result["refused"] == [str(tmp_path / "rate-1/rep-3")]


def test_saturation_must_land_within_10_percent_of_the_cap():
    found = {"saturation": {"achieved": 54.0, "offered": 70.0}}
    assert calibration._saturation(found, 50.0)["ok"]
    assert not calibration._saturation({"saturation": {"achieved": 56.0, "offered": 70.0}},
                                       50.0)["ok"]
    assert not calibration._saturation({"saturation": None}, 50.0)["ok"]


def test_a_stall_must_reach_the_tail_without_moving_the_median(tmp_path):
    def stall(name, top, p50, refused=False):
        path = tmp_path / name
        path.mkdir(parents=True)
        (path / "report.json").write_text(json.dumps({"refused": refused, "intervals": {
            "requested->constructed": {"max": top, "p50": p50}}}))

    stall("rate-1/rep-1", 510 * MS, 3 * MS)
    stall("rate-1/rep-2", 1, 1, refused=True)
    assert calibration._stall(tmp_path, 500.0)["ok"]
    stall("rate-1/rep-3", 400 * MS, 3 * MS)
    assert not calibration._stall(tmp_path, 500.0)["ok"]


@pytest.mark.calibration
async def test_the_live_calibration_passes_the_gate_a_loaded_host_cannot_fail(tmp_path):
    result = await calibration.calibrate(SCENARIOS, tmp_path, harness_commit="test",
                                         gate="bounds")
    assert result["bounds"]["ok"], json.dumps(result["bounds"], indent=1)
    assert result["ok"] and result["gate"] == "bounds"
    assert sorted(p.name for p in (tmp_path / "scenarios").iterdir()) == [
        f"{name}.toml" for name in calibration.SCENARIOS]
    assert json.loads((tmp_path / "calibration.json").read_text()) == result


def test_the_recorded_calibration_passes_the_strict_gate_with_this_code(tmp_path):
    result = calibration.replay(recording(tmp_path), tmp_path / "out", harness_commit="test")
    failing = {run: {k: e for k, e in res.items() if e.get("measured") and not e["ok"]}
               for run, res in result["intervals"]["runs"].items()}
    assert result["intervals"]["ok"], json.dumps(failing, indent=1)
    assert result["bounds"]["ok"] and result["saturation"]["ok"] and result["stall"]["ok"]
    assert result["ok"] and result["strict_ok"] and result["gate"] == "strict"
    assert len(result["bounds"]["intervals"]["runs"]) == 3
    offset = result["bounds"]["intervals"]["runs"][str(tmp_path / "out" / REP)]["offsets"]
    assert offset["stack:synthetic-issuer"]["skew_ms"] == 7300.0


def test_a_constant_the_harness_adds_passes_the_bounds_and_fails_the_strict_gate(tmp_path):
    # Shrinking the truth by 8 ms is what a harness adding 8 ms to every delivered->stored would
    # look like: above the 5 ms floor and 5% of a 60 ms median, and invisible to a lower bound.
    source = recording(tmp_path)
    for rep in (1, 2, 3):
        def shrink(lines):
            for line in lines:
                if (line["start"], line["end"]) == ("delivered", "stored"):
                    line["elapsed_ns"] -= 8 * MS
                    line["floor_ns"] -= 8 * MS
            return lines
        rewrite_jsonl(source / f"intervals/rate-15/rep-{rep}/truth.jsonl", shrink)
    strict = calibration.replay(source, tmp_path / "strict", harness_commit="test")
    assert strict["bounds"]["ok"] and not strict["intervals"]["ok"] and not strict["ok"]
    bounds = calibration.replay(source, tmp_path / "bounds", harness_commit="test",
                                gate="bounds")
    assert bounds["ok"] and not bounds["strict_ok"]


def test_a_measured_interval_shorter_than_its_floor_breaks_the_bounds(tmp_path):
    source = recording(tmp_path)

    def lengthen(lines):
        lines[0]["floor_ns"] += 10_000 * MS
        return lines
    rewrite_jsonl(source / REP / "truth.jsonl", lengthen)
    first = json.loads((source / REP / "truth.jsonl").read_text().splitlines()[0])
    result = calibration.replay(source, tmp_path / "out", harness_commit="test", gate="bounds")
    entry = result["bounds"]["intervals"]["runs"][str(tmp_path / "out" / REP)]["transitions"][
        f"{first['start']}->{first['end']}"]
    assert entry["short"] == 1 and entry["least_slack_ns"] < 0 and not entry["ok"]
    assert not result["ok"]


def test_an_injected_transition_with_no_measured_interval_breaks_the_bounds(tmp_path):
    source = recording(tmp_path)
    rewrite_jsonl(source / REP / "timeline.jsonl", lambda lines: [
        line for line in lines if not (line.get("name") == "stored"
                                       and line.get("credentials") == ["c000000"])])
    path = tmp_path / "out"
    result = calibration.replay(source, path, harness_commit="test", gate="bounds")
    entry = result["bounds"]["intervals"]["runs"][str(path / REP)]["transitions"]
    assert entry["delivered->stored"]["unmeasured"] == 1
    assert entry["stored->proof_generated"]["unmeasured"] == 1
    assert not result["ok"]


def test_an_injected_transition_with_no_truth_at_all_breaks_the_bounds(tmp_path):
    source = recording(tmp_path)
    rewrite_jsonl(source / REP / "truth.jsonl", lambda lines: [
        line for line in lines if (line["start"], line["end"]) != ("constructed",
                                                                   "registry_updated")])
    calibration.replay(source, tmp_path / "out", harness_commit="test", gate="bounds")
    run = calibration.bracket(tmp_path / "out" / REP)
    entry = run["transitions"]["constructed->registry_updated"]
    assert entry["count"] == 0 and not entry["ok"] and "no line" in entry["reason"]
    assert not run["ok"]
    # The verifier's sync delay is injected but this scenario never syncs: no truth is owed.
    assert "sync_full_started->sync_full_done" not in run["transitions"]


def test_a_milestone_removed_with_its_truth_still_owes_its_neighbours_truth(tmp_path):
    source = recording(tmp_path)
    rewrite_jsonl(source / REP / "timeline.jsonl", lambda lines: [
        line for line in lines if line.get("name") != "registry_updated"])
    rewrite_jsonl(source / REP / "truth.jsonl", lambda lines: [
        line for line in lines if "registry_updated" not in (line["start"], line["end"])])
    calibration.replay(source, tmp_path / "out", harness_commit="test", gate="bounds")
    run = calibration.bracket(tmp_path / "out" / REP)
    assert not run["transitions"]["constructed->registry_updated"]["ok"]
    assert not run["transitions"]["registry_updated->anchored"]["ok"] and not run["ok"]


def test_roles_sharing_a_clock_must_each_agree_with_its_offset(tmp_path):
    source = recording(tmp_path)
    record = json.loads((source / REP / "provenance.json").read_text())
    record["adapters"]["holder"]["milestones"]["stored"]["clock"] = "stack:synthetic-issuer"
    record["knobs"]["holder"]["clock_skew_ms"] = 7400.0
    (source / REP / "provenance.json").write_text(json.dumps(record))
    calibration.replay(source, tmp_path / "out", harness_commit="test", gate="bounds")
    offset = calibration.bracket(tmp_path / "out" / REP)["offsets"]["stack:synthetic-issuer"]
    assert offset["roles"]["issuer"]["ok"] and not offset["roles"]["holder"]["ok"]
    assert not offset["ok"]
    record["knobs"]["holder"]["clock_skew_ms"] = 7300.0
    (tmp_path / "out" / REP / "provenance.json").write_text(json.dumps(record))
    assert calibration.bracket(tmp_path / "out" / REP)["offsets"]["stack:synthetic-issuer"]["ok"]


def test_a_stack_clock_no_role_declares_breaks_the_bounds(tmp_path):
    source = recording(tmp_path)
    record = json.loads((source / REP / "provenance.json").read_text())
    for milestone in record["adapters"]["issuer"]["milestones"].values():
        if milestone["clock"].startswith("stack:"):
            milestone["clock"] = "harness"
    (source / REP / "provenance.json").write_text(json.dumps(record))
    calibration.replay(source, tmp_path / "out", harness_commit="test", gate="bounds")
    run = calibration.bracket(tmp_path / "out" / REP)
    offset = run["offsets"]["stack:synthetic-issuer"]
    assert not offset["ok"] and "no role declares" in offset["reason"] and not run["ok"]


def test_a_calibration_missing_a_repetition_fails_both_gates(tmp_path):
    source = recording(tmp_path)
    for name in ("timeline.jsonl", "provenance.json", "truth.jsonl"):
        (source / "stall/rate-20/rep-3" / name).unlink(missing_ok=True)
    (source / "stall/rate-20/rep-3").rmdir()
    result = calibration.replay(source, tmp_path / "out", harness_commit="test", gate="bounds")
    assert result["bounds"]["complete"] == {
        "ok": False, "missing": [str(tmp_path / "out/stall/rate-20/rep-3")]}
    assert not result["ok"] and not result["strict_ok"]


def test_a_damaged_truth_line_is_refused_with_a_code(tmp_path):
    path = tmp_path / "r"
    path.mkdir()
    (path / "truth.jsonl").write_text('{"credential": "c0"}\n{bad\n')
    with pytest.raises(BenchError) as refused:
        calibration.compare(path)
    assert refused.value.code == "e.input.format.calibration.truth.f"
    assert "Line 2" in refused.value.message


def test_a_repeated_milestone_is_bracketed_from_its_first_sighting(tmp_path):
    source = recording(tmp_path)

    def repeat(lines):
        stored = next(line for line in lines if line.get("name") == "stored")
        return lines + [{**stored, "t": stored["t"] + 10_000 * MS}]
    rewrite_jsonl(source / REP / "timeline.jsonl", repeat)
    calibration.replay(source, tmp_path / "out", harness_commit="test", gate="bounds")
    assert calibration.bracket(tmp_path / "out" / REP)["ok"]


def test_an_offset_that_disagrees_with_the_injected_skew_breaks_the_bounds(tmp_path):
    source = recording(tmp_path)
    record = json.loads((source / REP / "provenance.json").read_text())
    record["knobs"]["issuer"]["clock_skew_ms"] = 7299.0
    (source / REP / "provenance.json").write_text(json.dumps(record))
    calibration.replay(source, tmp_path / "out", harness_commit="test", gate="bounds")
    run = calibration.bracket(tmp_path / "out" / REP)
    offset = run["offsets"]["stack:synthetic-issuer"]
    assert abs(offset["error_ns"]) > offset["bound_ns"] and not offset["ok"] and not run["ok"]


@pytest.mark.parametrize("change", ["no skew", "no realtime offset"])
def test_an_offset_with_nothing_to_check_it_against_breaks_the_bounds(tmp_path, change):
    source = recording(tmp_path)
    if change == "no skew":
        record = json.loads((source / REP / "provenance.json").read_text())
        record["knobs"]["issuer"]["clock_skew_ms"] = "n/a"
        (source / REP / "provenance.json").write_text(json.dumps(record))
    else:
        rewrite_jsonl(source / REP / "timeline.jsonl", lambda lines: [
            line for line in lines if not (line.get("name") == "clock_offset"
                                           and line["attrs"]["domain"].startswith("realtime@"))])
    calibration.replay(source, tmp_path / "out", harness_commit="test", gate="bounds")
    run = calibration.bracket(tmp_path / "out" / REP)
    assert run["offsets"]["stack:synthetic-issuer"]["ok"] is False
    assert "reason" in run["offsets"]["stack:synthetic-issuer"] and not run["ok"]


def test_the_bounds_stall_needs_the_slowest_construction_to_reach_the_stall(tmp_path):
    def stall(name, top, resolution, refused=False):
        path = tmp_path / name
        path.mkdir(parents=True)
        (path / "report.json").write_text(json.dumps({"refused": refused, "intervals": {
            "requested->constructed": {"max": top, "p50": 3 * MS,
                                       "resolution_ns": resolution}}}))

    stall("rate-1/rep-1", 499 * MS, 1 * MS)    # 499 ms, within its 1 ms resolution of 500 ms
    stall("rate-1/rep-2", 1, 1, refused=True)
    assert calibration._stall_reached(tmp_path, 500.0)["ok"]
    stall("rate-1/rep-3", 498 * MS, 1 * MS)
    assert not calibration._stall_reached(tmp_path, 500.0)["ok"]
    assert not calibration._stall_reached(tmp_path / "none", 500.0)["ok"]


def test_the_bounds_saturation_needs_a_saturation_no_faster_than_the_cap_allows():
    assert calibration._saturation_capped({"saturation": {"achieved": 30.0, "offered": 50.0}},
                                          50.0)["ok"]   # a loaded host saturates early: fine
    assert calibration._saturation_capped({"saturation": {"achieved": 55.0, "offered": 70.0}},
                                          50.0)["ok"]
    assert not calibration._saturation_capped({"saturation": {"achieved": 56.0, "offered": 70.0}},
                                              50.0)["ok"]
    assert not calibration._saturation_capped({"saturation": None}, 50.0)["ok"]


def test_a_calibration_without_its_scenarios_cannot_be_judged(tmp_path):
    with pytest.raises(BenchError) as refused:
        calibration.judge(tmp_path, harness_commit="test")
    assert refused.value.code == "e.state.missing.calibration.scenarios.f"


async def test_a_gate_must_be_one_the_calibration_knows(tmp_path):
    for attempt in (lambda: calibration.judge(tmp_path, harness_commit="t", gate="loose"),
                    lambda: calibration.replay(tmp_path, tmp_path / "o", harness_commit="t",
                                               gate="loose")):
        with pytest.raises(BenchError) as refused:
            attempt()
        assert refused.value.code == "e.input.range.calibration.gate.f"
    with pytest.raises(BenchError):
        await calibration.calibrate(SCENARIOS, tmp_path, harness_commit="t", gate="loose")


def test_an_injected_transition_the_report_did_not_measure_fails_the_calibration(tmp_path):
    run_dir(tmp_path, "rate-1/rep-1", {("a", "b"): [MS]}, {})
    result = calibration._intervals(tmp_path)
    assert not result["ok"] and result["budget"] == {}
