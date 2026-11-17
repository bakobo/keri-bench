"""The calibration: proof that the harness measures correctly (test plan, Part B #1; this.i
6he49n0k, 0hykugwr).

Three scenarios run against the synthetic stack, and the result is judged at two gates.

The bounds gate checks what a loaded host cannot move. Every injected transition is measured for
every credential, and each measured interval plus its resolution is at least what had elapsed
inside the role when it began the effect ending the transition (``floor_ns``), since every
observation brackets that much of the role's work. Each offset to a skewed stack clock
agrees with the realtime offset less the injected skew, within the two offsets' bounds. The stall
reaches the tail, and the saturation detector fires at no more than the cap plus 10%.

The strict gate adds the two-sided bars: ``calibration-intervals`` must recover every injected
interval's p50 and p99 within the larger of 5% or the interval's resolution;
``calibration-saturation`` must put the saturation point within 10% of the known construction
cap; ``calibration-stall`` must show its stall in the tail without moving the median. Those
depend on how busy the host is, so they gate a recorded calibration (``replay``) and the bench
host, while CI's live run is gated on bounds. The result, with the measured error per transition,
is the error budget a real run carries in its provenance.
"""

from __future__ import annotations

import json
import math
import shutil
from collections import defaultdict
from pathlib import Path

from .clocks import Clocks, Interval, interval
from .errors import BenchError
from .report import write_report
from .run import summarize, sweep
from .scenario import load_scenario
from .timeline import read_timeline

INTERVAL_TOLERANCE = 0.05
SATURATION_TOLERANCE = 0.10
GATES = ("strict", "bounds")
SCENARIOS = ("calibration-intervals", "calibration-saturation", "calibration-stall")

E_TRUTH = "e.state.missing.calibration.truth.f"
E_GATE = "e.input.range.calibration.gate.f"
E_RECORDING = "e.state.missing.calibration.scenarios.f"


def _rank(values: list[int], p: float) -> int:
    return values[math.ceil(p / 100 * len(values)) - 1]


def compare(run_dir: Path) -> dict:
    """Each injected transition's truth against what the run's report measured."""
    run_dir = Path(run_dir)
    truth: dict[str, list[int]] = defaultdict(list)
    for item in _truth(run_dir):
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


def _truth(run_dir: Path) -> list[dict]:
    truth_path = run_dir / "truth.jsonl"
    if not truth_path.exists():
        raise BenchError(E_TRUTH, f"{run_dir} has no truth.jsonl, so it was not a calibration "
                         f"run and cannot be compared.")
    return [json.loads(line) for line in truth_path.read_text().splitlines()]


def bracket(run_dir: Path) -> dict:
    """Each credential's measured interval against its truth's floor, and each skewed
    stack clock's measured offset against the skew injected into it.

    Neither can be moved by a busy host. The harness stamps a transition's start before it asks
    the role and its end once the role's effect is seen (or on the role's own clock around the
    role's work), so the measured interval, widened by its resolution and offset bounds, contains
    the floor however late anything ran. Both offsets are bracketed reads taken on one host,
    so the realtime offset less the skew must fall within their bounds of the stack's.
    """
    run_dir = Path(run_dir)
    truth = _truth(run_dir)
    header, events = read_timeline(run_dir / "timeline.jsonl")
    record = json.loads((run_dir / "provenance.json").read_text())
    clocks = Clocks.from_events(header.harness_clock, events)
    first: dict[str, dict] = defaultdict(dict)
    for event in events:
        for credential in event.credentials:
            seen = first[credential].get(event.name)
            if seen is None or event.t < seen.t:
                first[credential][event.name] = event
    transitions: dict[str, dict] = {}
    for item in truth:
        start, end = item["start"], item["end"]
        entry = transitions.setdefault(f"{start}->{end}", {"count": 0, "unmeasured": 0,
                                                            "short": 0, "least_slack_ns": None})
        entry["count"] += 1
        names = first[item["credential"]]
        result = (interval(names[start], names[end], clocks)
                  if start in names and end in names else None)
        if not isinstance(result, Interval):
            entry["unmeasured"] += 1
            continue
        slack = result.value_ns + result.resolution_ns - item["floor_ns"]
        entry["short"] += slack < 0
        if entry["least_slack_ns"] is None or slack < entry["least_slack_ns"]:
            entry["least_slack_ns"] = slack
    for entry in transitions.values():
        entry["ok"] = entry["unmeasured"] == 0 and entry["short"] == 0
    offsets = _offsets(record, clocks)
    ok = bool(transitions) and all(e["ok"] for e in [*transitions.values(), *offsets.values()])
    return {"ok": ok, "transitions": transitions, "offsets": offsets}


def _offsets(record: dict, clocks: Clocks) -> dict:
    realtime = clocks.offsets.get(record["clocks"]["realtime"])
    out = {}
    for role, declared in sorted(record["adapters"].items()):
        domains = sorted({m["clock"] for m in declared["milestones"].values()
                          if m["clock"].startswith("stack:")})
        skew = record["knobs"].get(role, {}).get("clock_skew_ms")
        for domain in domains:
            measured = clocks.offsets.get(domain)
            if measured is None or realtime is None or not isinstance(skew, (int, float)):
                out[domain] = {"ok": False, "reason": "The run has no offset to this domain, "
                               "no realtime offset, or no injected skew to check it against."}
                continue
            error = measured.offset_ns - (realtime.offset_ns - round(skew * 1e6))
            bound = measured.bound_ns + realtime.bound_ns
            out[domain] = {"skew_ms": skew, "error_ns": error, "bound_ns": bound,
                           "ok": abs(error) <= bound}
    return out


def _accepted(out: Path) -> tuple[list[Path], list[str]]:
    accepted, refused = [], []
    for run_dir in _run_dirs(out):
        if json.loads((run_dir / "report.json").read_text()).get("refused"):
            refused.append(str(run_dir))
        else:
            accepted.append(run_dir)
    return accepted, refused


def _run_dirs(out: Path) -> list[Path]:
    def order(path: Path) -> tuple[float, int]:
        return float(path.parent.name.removeprefix("rate-")), int(path.name.removeprefix("rep-"))
    return sorted(Path(out).glob("rate-*/rep-*"), key=order)


def _bracketed(out: Path) -> dict:
    accepted, refused = _accepted(out)
    runs = {str(run_dir): bracket(run_dir) for run_dir in accepted}
    return {"ok": bool(runs) and all(r["ok"] for r in runs.values()), "runs": runs,
            "refused": refused}


def _intervals(out: Path) -> dict:
    accepted, refused = _accepted(out)
    comparisons = {str(run_dir): compare(run_dir) for run_dir in accepted}
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
    for run_dir in _accepted(out)[0]:
        measured = json.loads((run_dir / "report.json").read_text())["intervals"][
            "requested->constructed"]
        runs[str(run_dir)] = {"max_ns": measured["max"], "p50_ns": measured["p50"],
                              "ok": measured["max"] >= stall_ms * 1e6
                              and measured["p50"] < stall_ms * 1e6 / 2}
    return {"ok": bool(runs) and all(r["ok"] for r in runs.values()), "stall_ms": stall_ms,
            "runs": runs}


def _stall_reached(out: Path, stall_ms: float) -> dict:
    """The bounds gate's stall: the slowest construction, widened by its resolution, is at least
    the stall. A credential queued behind the stall waited all of it, however busy the host."""
    runs = {}
    for run_dir in _accepted(out)[0]:
        measured = json.loads((run_dir / "report.json").read_text())["intervals"][
            "requested->constructed"]
        top = measured["max"] + measured["resolution_ns"]
        runs[str(run_dir)] = {"max_ns": measured["max"],
                              "resolution_ns": measured["resolution_ns"],
                              "ok": top >= stall_ms * 1e6}
    return {"ok": bool(runs) and all(r["ok"] for r in runs.values()), "stall_ms": stall_ms,
            "runs": runs}


def _saturation_capped(summary: dict, cap: float) -> dict:
    """The bounds gate's saturation: the detector fires, and at no more than the cap plus 10%.
    The issuer cannot construct faster than its service rate, and a busy host only slows it."""
    found = summary["saturation"]
    if found is None:
        return {"ok": False, "cap": cap, "reason": "No load step saturated."}
    return {"ok": found["achieved"] <= cap * (1 + SATURATION_TOLERANCE), "cap": cap,
            "achieved_at_saturation": found["achieved"], "offered": found["offered"]}


def _reports(out: Path) -> dict[float, list[dict]]:
    reports: dict[float, list[dict]] = defaultdict(list)
    for run_dir in _run_dirs(out):
        reports[float(run_dir.parent.name.removeprefix("rate-"))].append(
            json.loads((run_dir / "report.json").read_text()))
    return reports


def _check_gate(gate: str) -> None:
    if gate not in GATES:
        raise BenchError(E_GATE, f"A calibration gate is one of {list(GATES)}, not {gate!r}.")


def judge(out: Path, *, harness_commit: str, gate: str = "strict") -> dict:
    """Both gates over a calibration's output on disk; ``ok`` is the named gate's verdict.

    The strict gate includes the bounds gate, since a harness that passes the two-sided bars
    while violating a bound got there by luck."""
    _check_gate(gate)
    out = Path(out)
    scenarios = out / "scenarios"
    if not all((scenarios / f"{name}.toml").exists() for name in SCENARIOS):
        raise BenchError(E_RECORDING, f"{out} has no copy of the calibration scenarios in "
                         f"{scenarios}, so its runs cannot be judged against what they ran.")
    saturating = load_scenario(scenarios / "calibration-saturation.toml")
    stalling = load_scenario(scenarios / "calibration-stall.toml")
    summary = summarize(saturating, _reports(out / "saturation"))
    cap = saturating.roles["issuer"].knobs["service_rate"]
    stall_ms = stalling.roles["issuer"].knobs["stall_ms"]
    bounds = {"intervals": _bracketed(out / "intervals"),
              "saturation": _saturation_capped(summary, cap),
              "stall": _stall_reached(out / "stall", stall_ms)}
    bounds["ok"] = all(bounds[k]["ok"] for k in ("intervals", "saturation", "stall"))
    result = {
        "harness_commit": harness_commit,
        "gate": gate,
        "bounds": bounds,
        "intervals": _intervals(out / "intervals"),
        "saturation": _saturation(summary, cap),
        "stall": _stall(out / "stall", stall_ms),
    }
    result["strict_ok"] = bounds["ok"] and all(result[k]["ok"]
                                               for k in ("intervals", "saturation", "stall"))
    result["ok"] = result["strict_ok"] if gate == "strict" else bounds["ok"]
    (out / "calibration.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


async def calibrate(scenarios: Path, out: Path, *, harness_commit: str,
                    gate: str = "strict") -> dict:
    """Run the three calibration scenarios live, keep a copy of them beside their runs so the
    calibration can be replayed, and judge it at ``gate``."""
    _check_gate(gate)
    scenarios, out = Path(scenarios), Path(out)
    (out / "scenarios").mkdir(parents=True, exist_ok=True)
    for name in SCENARIOS:
        shutil.copyfile(scenarios / f"{name}.toml", out / "scenarios" / f"{name}.toml")
    for name, sub in zip(SCENARIOS, ("intervals", "saturation", "stall")):
        await sweep(load_scenario(out / "scenarios" / f"{name}.toml"), out / sub,
                    harness_commit=harness_commit)
    return judge(out, harness_commit=harness_commit, gate=gate)


def replay(recording: Path, out: Path, *, harness_commit: str, gate: str = "strict") -> dict:
    """Judge a recorded calibration with this code: copy it, rebuild every run's report from its
    timeline and provenance, and judge the copy. Nothing is timed, so the strict gate holds the
    analysis to the full two-sided bars with no dependence on the host it runs on."""
    _check_gate(gate)
    out = Path(out)
    shutil.copytree(recording, out)
    for sub in ("intervals", "saturation", "stall"):
        for run_dir in _run_dirs(out / sub):
            write_report(run_dir, generator_commit=harness_commit)
    return judge(out, harness_commit=harness_commit, gate=gate)
