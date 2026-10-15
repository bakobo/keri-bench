"""The calibration: proof that the harness measures correctly (test plan, Part B #1; this.i
6he49n0k).

Three scenarios run against the synthetic stack. ``calibration-intervals`` must recover every
injected interval's p50 and p99 within the larger of 5% or the interval's resolution;
``calibration-saturation`` must put the saturation point within 10% of the known construction
cap; ``calibration-stall`` must show a stall it injected in the tail. The result, with the
measured error per transition, is the error budget a real run carries in its provenance.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

from .errors import BenchError
from .run import sweep
from .scenario import load_scenario

INTERVAL_TOLERANCE = 0.05
SATURATION_TOLERANCE = 0.10

E_TRUTH = "e.state.missing.calibration.truth.f"


def _rank(values: list[int], p: float) -> int:
    return values[math.ceil(p / 100 * len(values)) - 1]


def compare(run_dir: Path) -> dict:
    """Each injected transition's truth against what the run's report measured."""
    run_dir = Path(run_dir)
    truth_path = run_dir / "truth.jsonl"
    if not truth_path.exists():
        raise BenchError(E_TRUTH, f"{run_dir} has no truth.jsonl, so it was not a calibration "
                         f"run and cannot be compared.")
    truth: dict[str, list[int]] = defaultdict(list)
    for line in truth_path.read_text().splitlines():
        item = json.loads(line)
        truth[f"{item['start']}->{item['end']}"].append(item["elapsed_ns"])
    report = json.loads((run_dir / "report.json").read_text())
    out = {}
    for key, values in sorted(truth.items()):
        measured = report["intervals"].get(key)
        if measured is None or not measured.get("measured"):
            out[key] = {"measured": False}
            continue
        values.sort()
        entry = {"measured": True, "count": len(values), "measured_count": measured["count"],
                 "resolution_ns": measured["resolution_ns"]}
        ok = len(values) == measured["count"]
        for p, name in ((50, "p50"), (99, "p99")):
            expected = _rank(values, p)
            tolerance = max(round(INTERVAL_TOLERANCE * expected), measured["resolution_ns"])
            error = measured[name] - expected
            entry[name] = {"truth_ns": expected, "measured_ns": measured[name],
                           "error_ns": error, "tolerance_ns": tolerance}
            ok = ok and abs(error) <= tolerance
        entry["ok"] = ok
        out[key] = entry
    return out


def _intervals(out: Path) -> dict:
    comparisons, refused = {}, []
    for run_dir in sorted(Path(out).glob("rate-*/rep-*")):
        report = json.loads((run_dir / "report.json").read_text())
        if report.get("refused"):
            refused.append(str(run_dir))
            continue
        comparisons[str(run_dir)] = compare(run_dir)
    budget: dict[str, dict] = {}
    for result in comparisons.values():
        for key, entry in result.items():
            if not entry.get("measured"):
                continue
            worst = budget.setdefault(key, {"p50_error_ns": 0, "p99_error_ns": 0})
            for name in ("p50", "p99"):
                error = entry[name]["error_ns"]
                if abs(error) > abs(worst[f"{name}_error_ns"]):
                    worst[f"{name}_error_ns"] = error
    measured = [entry for result in comparisons.values() for entry in result.values()]
    ok = bool(comparisons) and all(entry.get("ok") for entry in measured)
    return {"ok": ok, "runs": comparisons, "refused": refused, "budget": budget}


def _saturation(summary: dict, cap: float) -> dict:
    found = summary["saturation"]
    if found is None:
        return {"ok": False, "cap": cap, "reason": "No load step saturated."}
    error = (found["achieved"] - cap) / cap
    return {"ok": abs(error) <= SATURATION_TOLERANCE, "cap": cap,
            "achieved_at_saturation": found["achieved"], "offered": found["offered"],
            "relative_error": error}


def _stall(out: Path, stall_ms: float) -> dict:
    runs = {}
    for run_dir in sorted(Path(out).glob("rate-*/rep-*")):
        report = json.loads((run_dir / "report.json").read_text())
        if report.get("refused"):
            continue
        interval = report["intervals"]["requested->constructed"]
        runs[str(run_dir)] = {"max_ns": interval["max"], "p50_ns": interval["p50"],
                              "ok": interval["max"] >= stall_ms * 1e6
                              and interval["p50"] < stall_ms * 1e6 / 2}
    return {"ok": bool(runs) and all(r["ok"] for r in runs.values()), "stall_ms": stall_ms,
            "runs": runs}


async def calibrate(scenarios: Path, out: Path, *, harness_commit: str) -> dict:
    scenarios, out = Path(scenarios), Path(out)
    intervals = load_scenario(scenarios / "calibration-intervals.toml")
    await sweep(intervals, out / "intervals", harness_commit=harness_commit)
    saturating = load_scenario(scenarios / "calibration-saturation.toml")
    summary = await sweep(saturating, out / "saturation", harness_commit=harness_commit)
    stalling = load_scenario(scenarios / "calibration-stall.toml")
    await sweep(stalling, out / "stall", harness_commit=harness_commit)
    result = {
        "harness_commit": harness_commit,
        "intervals": _intervals(out / "intervals"),
        "saturation": _saturation(summary, saturating.roles["issuer"].knobs["service_rate"]),
        "stall": _stall(out / "stall", stalling.roles["issuer"].knobs["stall_ms"]),
    }
    result["ok"] = all(result[k]["ok"] for k in ("intervals", "saturation", "stall"))
    (out / "calibration.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
