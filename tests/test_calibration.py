"""The calibration (Part B #1): the comparison logic on fixed inputs, then the real thing."""

import json
from pathlib import Path

import pytest

from keri_bench import calibration
from keri_bench.errors import BenchError

SCENARIOS = Path(__file__).parent.parent / "scenarios"
MS = 1_000_000


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


def test_compare_holds_each_transition_to_the_larger_of_5_percent_or_its_resolution(tmp_path):
    # Hand-computed truth: 100 values 1..100 ms; p50 is the 50th (50 ms), p99 the 99th (99 ms).
    values = [i * MS for i in range(1, 101)]
    path = run_dir(tmp_path, "r", {("a", "b"): values, ("c", "d"): values, ("e", "f"): values,
                                   ("g", "h"): values}, {
        "a->b": interval(100, 52 * MS, 99 * MS),                   # +2 ms <= 2.5 ms: ok
        "c->d": interval(100, 53 * MS, 99 * MS),                   # +3 ms > 2.5 ms: not ok
        "e->f": interval(100, 54 * MS, 99 * MS, resolution=5 * MS),  # 4 ms <= resolution 5 ms
        "g->h": interval(0, 0, 0, measured=False)})
    result = calibration.compare(path)
    assert result["a->b"]["ok"] and result["e->f"]["ok"] and not result["c->d"]["ok"]
    assert result["a->b"]["p50"] == {"truth_ns": 50 * MS, "measured_ns": 52 * MS,
                                     "error_ns": 2 * MS, "tolerance_ns": 2_500_000}
    assert result["e->f"]["p50"]["tolerance_ns"] == 5 * MS
    assert result["g->h"] == {"measured": False}


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
async def test_the_harness_recovers_what_the_calibration_stack_injected(tmp_path):
    result = await calibration.calibrate(SCENARIOS, tmp_path, harness_commit="test")
    detail = json.dumps({k: result[k] for k in ("saturation", "stall")}, indent=1)
    failing = {run: {k: e for k, e in res.items() if e.get("measured") and not e["ok"]}
               for run, res in result["intervals"]["runs"].items()}
    assert result["intervals"]["ok"], json.dumps(failing, indent=1)
    assert result["saturation"]["ok"], detail
    assert result["stall"]["ok"], detail
    assert result["ok"]
    assert json.loads((tmp_path / "calibration.json").read_text()) == result


def test_an_injected_transition_the_report_did_not_measure_fails_the_calibration(tmp_path):
    run_dir(tmp_path, "rate-1/rep-1", {("a", "b"): [MS]}, {})
    result = calibration._intervals(tmp_path)
    assert not result["ok"] and result["budget"] == {}
