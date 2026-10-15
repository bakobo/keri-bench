"""``report.json`` and ``report.md``, derived from the timeline and the provenance only.

Nothing here reads live state, so a disputed number can be recomputed from a run directory
(this.i 8q4myzru; test plan, Outputs). Every latency goes through the one named percentile
definition in ``stats`` (HDR, three significant digits, quoted as the bucket's upper bound), and
every interval through ``clocks.interval``, so a cross-domain interval without a measured offset
is reported as not measured rather than computed (this.i 7vsky4sk).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any

from . import provenance as provenance_file
from .clocks import Clocks, Interval, NotMeasured, interval
from .driver import check_driver
from .errors import BenchError
from .reconcile import classify, summary
from .stats import Histogram
from .timeline import Event, Header, read_timeline
from .vocabulary import ISSUANCE_CHAIN, MILESTONES, PAIRS, REVOCATION_CHAIN, Pair

REPORT_SCHEMA = 1
NOT_MEASURED = "not measured"
PERCENTILES: tuple[tuple[str, int | float], ...] = (("p50", 50), ("p90", 90), ("p99", 99),
                                                    ("p99.9", 99.9))
RUN_FIELDS = ("rate", "repetition", "warmup_s", "lag_limit_ms", "measure", "stages")
DRIVER = {"adapter": "driver", "role": "harness", "method": "observed", "clock": "harness",
          "resolution_ns": 1}
"""The driver writes ``requested`` itself, so it is observed even when no adapter declares it."""

E_WINDOW = "e.input.missing.timeline.window.f"
E_RUN = "e.input.missing.provenance.run.f"
E_MEASURE = "e.input.range.provenance.measure.f"
E_WARMUP = "e.input.range.provenance.warmup.f"
E_REQUESTS = "e.input.missing.timeline.requested.f"
E_REQUESTED_CLOCK = "e.input.range.timeline.requested.clock.f"


class _Run:
    """One run's timeline and provenance, indexed for the sections that read them."""

    def __init__(self, header: Header, events: list[Event], record: dict) -> None:
        self.header, self.events, self.record = header, events, record
        self.clocks = Clocks.from_events(header.harness_clock, events)
        self.run = record["run"]
        missing = [name for name in RUN_FIELDS if name not in self.run]
        if missing:
            raise BenchError(E_RUN, f"The provenance's run section lacks {missing}, so the "
                             f"report cannot say what was offered or measured.")
        measure = self.run["measure"]
        if not (isinstance(measure, list) and len(measure) == 2
                and all(name in MILESTONES for name in measure)):
            raise BenchError(E_MEASURE, f"The run's measure must name a start and an end "
                             f"milestone, and {measure!r} does not.")
        self.measure: tuple[str, str] = (measure[0], measure[1])
        self.start = self._first_harness_event("run_started").t
        self.end = self._first_harness_event("run_ended").t
        self.warmup = round(self.run["warmup_s"] * 1_000_000_000)
        self.measured_from = self.start + self.warmup
        if self.measured_from >= self.end:
            raise BenchError(E_WARMUP, f"The warmup of {self.warmup} ns is as long as the "
                             f"{self.end - self.start} ns window, so nothing was measured.")
        self.first: dict[str, dict[str, Event]] = defaultdict(dict)
        for event in events:
            for credential in event.credentials:
                seen = self.first[credential].get(event.name)
                if seen is None or event.t < seen.t:
                    self.first[credential][event.name] = event
        self.requested: dict[str, int] = {}
        for credential, names in sorted(self.first.items()):
            if "requested" in names:
                mapped = self.clocks.to_harness(names["requested"])
                if mapped is None:
                    raise BenchError(E_REQUESTED_CLOCK, f"{credential} was requested on "
                                     f"{names['requested'].clock}, which has no offset to the "
                                     f"harness clock, so its latency has no start.")
                self.requested[credential] = mapped[0]
        if not self.requested:
            raise BenchError(E_REQUESTS, "The timeline has no requested event, so there is "
                             "nothing to report.")
        self.window = [c for c, t in self.requested.items()
                       if self.measured_from <= t < self.end]
        self.observed = {event.name for event in events}

    def _first_harness_event(self, name: str) -> Event:
        found = [event for event in self.events if event.name == name]
        if not found:
            raise BenchError(E_WINDOW, f"The timeline has no {name} event, so the measured "
                             f"window is unknown.")
        return min(found, key=lambda event: event.t)

    def harness_t(self, event: Event) -> int | None:
        mapped = self.clocks.to_harness(event)
        return None if mapped is None else mapped[0]

    def samples(self, credentials: Iterable[str], start: str,
                end: str) -> tuple[list[Interval | NotMeasured], int]:
        results, missing = [], 0
        for credential in credentials:
            names = self.first[credential]
            if start not in names:
                continue
            if end not in names:
                missing += 1
                continue
            results.append(interval(names[start], names[end], self.clocks))
        return results, missing


def _reported(result: Interval) -> int:
    # reported() leaves a 1 ns-resolution negative alone; a report never shows one.
    return max(0, result.reported())


def _summarise(results: Sequence[Interval | NotMeasured], missing: int) -> dict:
    histogram, reasons, unmeasured, resolution = Histogram(), set(), 0, None
    for result in results:
        if isinstance(result, NotMeasured):
            unmeasured += 1
            reasons.add(result.reason)
            continue
        histogram.record(_reported(result))
        resolution = max(resolution or 0, result.resolution_ns)
    out: dict[str, Any] = {"count": histogram.count, "missing": missing,
                           "not_measured": {"count": unmeasured, "reasons": sorted(reasons)},
                           "resolution_ns": resolution, "measured": histogram.count > 0}
    if histogram.count:
        for key, p in PERCENTILES:
            out[key] = histogram.percentile(p)
        out["max"] = histogram.max
    return out


def _key(start: str, end: str) -> str:
    return f"{start}->{end}"


def _intervals(run: _Run) -> dict:
    specs: dict[str, list[str]] = {}
    for pair in PAIRS:
        for start, end in pair.intervals:
            specs.setdefault(_key(start, end), []).append(pair.key)
    specs.setdefault(_key(*run.measure), [])
    for chain in (ISSUANCE_CHAIN, REVOCATION_CHAIN):
        seen = [name for name in chain if name in run.observed or name == "requested"]
        for start, end in zip(seen, seen[1:]):
            specs.setdefault(_key(start, end), [])
    for name in MILESTONES:
        if name != "requested" and name in run.observed:
            specs.setdefault(_key("requested", name), [])
    out = {}
    # A cold sync is one per verifier, not one per credential, so its events name the sync rather
    # than any requested credential (pair 5, initial synchronization).
    syncs = sorted({c for c, names in run.first.items() if "sync_full_started" in names})
    for key, pairs in specs.items():
        start, end = key.split("->")
        entry = {"start": start, "end": end, "pairs": pairs}
        entry.update(_summarise(*run.samples(syncs if start == "sync_full_started"
                                             else run.window, start, end)))
        out[key] = entry
    return out


def _censored(run: _Run) -> dict:
    """Credentials lacking the measure end are censored at the window's end: a lower bound."""
    start, end = run.measure
    samples: list[tuple[int, bool]] = []
    incomplete = []
    for credential in run.window:
        names = run.first[credential]
        if end in names:
            if start in names:
                result = interval(names[start], names[end], run.clocks)
                if isinstance(result, Interval):
                    samples.append((_reported(result), False))
            continue
        incomplete.append(credential)
        begin = run.harness_t(names[start]) if start in names else None
        if begin is not None:
            samples.append((max(0, run.end - begin), True))
    out: dict[str, Any] = {"count": len(incomplete), "credentials": sorted(incomplete)}
    if not samples:
        out["latency"] = {"measured": False}
        return out
    histogram = Histogram()
    for value, _ in samples:
        histogram.record(value)
    # At a tie the censored sample sorts last: its true value is at least the recorded one.
    ordered = sorted(samples)
    latency: dict[str, Any] = {"count": len(samples), "censored": sum(c for _, c in samples)}
    for key, p in PERCENTILES:
        rank = math.ceil(Fraction(str(p)) * len(ordered) / 100)
        latency[key] = {"value": histogram.percentile(p), "lower_bound": ordered[rank - 1][1]}
    latency["max"] = {"value": histogram.max, "lower_bound": ordered[-1][1]}
    out["latency"] = latency
    return out


def _driver(run: _Run) -> dict:
    credentials = sorted(run.requested)
    dispatched = []
    for credential in credentials:
        event = run.first[credential].get("dispatched")
        dispatched.append(None if event is None else run.harness_t(event))
    limit = round(run.run["lag_limit_ms"] * 1_000_000)
    verdict = check_driver([run.requested[c] for c in credentials], dispatched,
                           lag_limit_ns=limit)
    return {"ok": verdict.ok, "p99_lag_ns": verdict.p99_lag_ns, "backlog": verdict.backlog,
            "reason": verdict.reason, "lag_limit_ns": limit}


def _throughput(run: _Run) -> dict:
    span = run.end - run.measured_from
    milestones = {}
    for name in MILESTONES:
        events = [names[name] for names in run.first.values() if name in names]
        if not events:
            continue
        count = unconvertible = 0
        for event in events:
            t = run.harness_t(event)
            if t is None:
                unconvertible += 1
            elif run.measured_from <= t < run.end:
                count += 1
        milestones[name] = {"count": count, "per_s": count * 1_000_000_000 / span,
                            "unconvertible": unconvertible}
    pairs = {pair.key: {name: milestones.get(name, {}).get("per_s")
                        for name in pair.throughput}
             for pair in PAIRS if pair.throughput}
    return {"window_ns": span, "offered_per_s": run.run["rate"], "milestones": milestones,
            "pairs": pairs}


def _fault_windows(run: _Run) -> list[dict]:
    ends = sorted((e for e in run.events if e.name == "fault_ended"), key=lambda e: e.t)
    used: set[int] = set()
    found = []
    for start in sorted((e for e in run.events if e.name == "fault_started"),
                        key=lambda e: e.t):
        attrs = start.attrs or {}
        kind, target = attrs.get("kind"), attrs.get("target")
        ended = None
        for i, end in enumerate(ends):
            other = end.attrs or {}
            if i not in used and end.t >= start.t and (other.get("kind"),
                                                      other.get("target")) == (kind, target):
                used.add(i)
                ended = end.t
                break
        found.append({"kind": kind, "target": target, "started_ns": start.t,
                      "ended_ns": ended})
    return found


def _faults(run: _Run, windows: list[dict]) -> list[dict]:
    start_name, end_name = run.measure
    out = []
    for window in windows:
        began = window["started_ns"]
        until = run.end if window["ended_ns"] is None else window["ended_ns"]
        groups: dict[str, list[str]] = {"before": [], "during": [], "after": []}
        for credential in run.window:
            t = run.requested[credential]
            groups["before" if t < began else "during" if t < until else "after"].append(
                credential)
        entry = dict(window)
        for phase, members in groups.items():
            entry[phase] = _summarise(*run.samples(members, start_name, end_name))
        if window["kind"] == "crash":
            entry.update(_recovery(run, window["target"], began))
        out.append(entry)
    return out


def _process_event(run: _Run, name: str, process: str, after: int) -> Event | None:
    found = [e for e in run.events if e.name == name and e.t >= after
             and (e.attrs or {}).get("process") == process]
    return min(found, key=lambda e: e.t) if found else None


def _recovery(run: _Run, target: str, began: int) -> dict:
    out: dict[str, int | None] = {"time_to_ready_ns": None, "time_to_first_success_ns": None}
    killed = _process_event(run, "process_killed", target, began)
    ready = killed and _process_event(run, "process_ready", target, killed.t)
    if ready is None:
        return out
    out["time_to_ready_ns"] = ready.t - killed.t
    successes = [t for e in run.events if e.name == run.measure[1]
                 for t in [run.harness_t(e)] if t is not None and t >= ready.t]
    if successes:
        out["time_to_first_success_ns"] = min(successes) - ready.t
    return out


def _matrix(run: _Run) -> dict:
    declared: dict[str, dict] = {}
    for role, declaration in run.record["adapters"].items():
        for name, seen in declaration.get("milestones", {}).items():
            declared.setdefault(name, {"adapter": declaration["adapter"], "role": role,
                                       "method": seen["method"], "clock": seen["clock"],
                                       "resolution_ns": seen["resolution_ns"]})
    declared.setdefault("requested", dict(DRIVER))
    counts: dict[str, int] = defaultdict(int)
    for event in run.events:
        counts[event.name] += 1
    return {name: {**declared[name], "events": counts[name]} if name in declared
            else "unobserved" for name in MILESTONES}


def _domain(run: _Run, matrix: dict, name: str) -> str:
    for event in run.events:
        if event.name == name:
            return event.clock
    clock = matrix[name]["clock"]
    if clock == "harness":
        return run.header.harness_clock
    return run.record["clocks"].get(clock, clock)


def _gaps(run: _Run, matrix: dict, names: Sequence[str],
          intervals: Iterable[tuple[str, str]], harness_bound: Iterable[str]) -> list[str]:
    gaps = [f"No adapter in this run observes {name}." for name in names
            if matrix[name] == "unobserved"]
    if gaps:
        return gaps

    def unmeasured(name: str) -> str | None:
        domain = _domain(run, matrix, name)
        if domain == run.header.harness_clock or domain in run.clocks.offsets:
            return None
        return (f"{name} is stamped in {domain}, which has no offset to the harness clock "
                f"measured in this run.")

    found = [unmeasured(name) for name in harness_bound]
    for start, end in intervals:
        if _domain(run, matrix, start) != _domain(run, matrix, end):
            found += [unmeasured(start), unmeasured(end)]
    found = list(dict.fromkeys(gap for gap in found if gap))
    if found:
        return found
    # Declared and measurable in principle, but a pair is measured only if it was in this run,
    # and an interval only if one credential has both of its ends.
    missing = [f"{name} is declared, but no {name} event appeared in this run."
               for name in names if name not in run.observed]
    return missing or [f"No credential in this run has both {start} and {end}."
                       for start, end in intervals
                       if not any(start in seen and end in seen
                                  for seen in run.first.values())]


def _ordered(pair: Pair) -> list[str]:
    names = [name for interval_ in pair.intervals for name in interval_]
    return list(dict.fromkeys(names + list(pair.throughput or ())))


def _observability(run: _Run, faults: list[dict]) -> dict:
    matrix = _matrix(run)
    pairs = {}
    for pair in PAIRS:
        if pair.intervals or pair.throughput:
            gaps = _gaps(run, matrix, _ordered(pair), pair.intervals, pair.throughput or ())
        else:
            gaps = _gaps(run, matrix, run.measure, [run.measure], ())
            if not faults:
                gaps.append("No fault was scheduled in this run, so there is no faulted "
                            "performance to set beside normal performance.")
        pairs[pair.key] = {"measurable": not gaps, "gap": " ".join(gaps) or None}
    return {"milestones": matrix, "pairs": pairs}


def _failures(events) -> dict:
    """Lifecycle stages that failed, by stage and code, and a scheduled fault that could not be
    applied, which refuses the run: its numbers would describe a fault that never happened."""
    stages: dict[str, int] = {}
    codes: dict[str, int] = {}
    fault_failed = None
    for event in events:
        attrs = event.attrs or {}
        if event.name == "stage_failed":
            # Attributes are whatever the timeline says, so they are named as text.
            stage, code = str(attrs.get("stage", "?")), str(attrs.get("code", "?"))
            stages[stage] = stages.get(stage, 0) + 1
            codes[code] = codes.get(code, 0) + 1
        elif event.name == "fault_failed" and fault_failed is None:
            fault_failed = (f"The scheduled {attrs.get('kind')} fault on {attrs.get('target')} "
                            f"could not be applied: {attrs.get('error')}")
    return {"count": sum(stages.values()), "by_stage": stages, "by_code": codes,
            "fault_failed": fault_failed}


def _warnings(run: _Run, driver: dict, intervals: dict) -> list[str]:
    out = []
    for item in provenance_file.untested(run.record["adapters"], run.record["components"]):
        tested = item["tested"] or "no version of it"
        out.append(f"The {item['role']} adapter {item['adapter']} ran against "
                   f"{item['component']} {item['running']}, which it was never "
                   f"contract-tested against (it was tested against {tested}).")
    if not driver["ok"]:
        out.append(f"The run is refused. {driver['reason']}")
    for key, entry in intervals.items():
        skipped = entry["not_measured"]
        if skipped["count"]:
            out.append(f"The interval {key} was not measured for {skipped['count']} "
                       f"credential(s) because of its clock evidence. "
                       + " ".join(skipped["reasons"]))
    return out


def derive(timeline_path: Path, provenance_path: Path, *, generator_commit: str) -> dict:
    header, events = read_timeline(timeline_path)
    record = provenance_file.read(provenance_path)
    run = _Run(header, events, record)
    driver = _driver(run)
    intervals = _intervals(run)
    windows = _fault_windows(run)
    reconciliation: Any = NOT_MEASURED
    if "reconciliation" in record:
        section = record["reconciliation"]
        spans = [(w["started_ns"], run.end if w["ended_ns"] is None else w["ended_ns"])
                 for w in windows]
        outcomes = classify(events, section["verifier"], section["anchors"], faults=spans)
        reconciliation = {"summary": summary(outcomes),
                          "credentials": {c: outcomes[c].to_dict() for c in sorted(outcomes)}}
    failures = _failures(events)
    refused = driver["reason"] if not driver["ok"] else failures["fault_failed"]
    out = {
        "report_schema": REPORT_SCHEMA, "generator_commit": generator_commit,
        "timeline_schema": header.timeline_schema,
        "vocabulary_version": header.vocabulary_version,
        "harness_commit": header.harness_commit, "scenario_sha256": record["scenario_sha256"],
        "run": run.run,
        "window": {"start_ns": run.start, "end_ns": run.end, "warmup_ns": run.warmup,
                   "measured_from_ns": run.measured_from, "credentials": len(run.window)},
        "driver": driver, "refused": refused is not None, "refused_reason": refused,
        "failures": failures,
        "measure": _key(*run.measure), "intervals": intervals, "incomplete": _censored(run),
        "throughput": _throughput(run), "observability": _observability(run, windows),
        "faults": _faults(run, windows), "reconciliation": reconciliation,
        "measurement_cost": record.get("measurement_cost", NOT_MEASURED),
        "census": NOT_MEASURED, "warnings": _warnings(run, driver, intervals),
    }
    if failures["fault_failed"]:
        out["warnings"].append(f"The run is refused. {failures['fault_failed']}")
    if failures["count"]:
        out["warnings"].append(f"{failures['count']} lifecycle stage(s) failed: "
                               f"{failures['by_stage']}, with codes {failures['by_code']}.")
    # Provenance written before calibration existed has no section, and its report is unchanged.
    if "calibration" in record:
        out["calibration"] = record["calibration"]
        out["warnings"] += _calibration_warnings(record["calibration"], header.harness_commit)
    return out


def _calibration_warnings(calibration: Any, harness_commit: str) -> list[str]:
    if not isinstance(calibration, dict):
        return ["The run carries no calibration, so its numbers have no measured error budget "
                "(test plan, Part B #1). Run keri-bench calibrate and pass --calibration."]
    out = []
    if not calibration["ok"]:
        out.append("The calibration this run carries did not pass, so its error budget does not "
                   "show that the harness measures correctly.")
    if calibration["harness_commit"] != harness_commit:
        out.append(f"The calibration was made by harness {calibration['harness_commit']}, not "
                   f"by {harness_commit}, which made this run.")
    return out


# --- markdown ----------------------------------------------------------------------------------

def _ns(value: int | None) -> str:
    return "not seen" if value is None else f"{value} ns"


def _interval_table(rows: Iterable[tuple[str, dict]]) -> list[str]:
    lines = ["| interval | n | missing | not measured | resolution | p50 | p90 | p99 | p99.9 "
             "| max |", "|---|---|---|---|---|---|---|---|---|---|"]
    for key, entry in rows:
        if entry["measured"]:
            values = " | ".join(_ns(entry[k]) for k in ("p50", "p90", "p99", "p99.9", "max"))
            resolution = _ns(entry["resolution_ns"])
        else:
            values, resolution = " | ".join([NOT_MEASURED] * 5), "-"
        lines.append(f"| {key} | {entry['count']} | {entry['missing']} | "
                     f"{entry['not_measured']['count']} | {resolution} | {values} |")
    return lines


def _pairs_md(report: dict) -> list[str]:
    lines = ["## The seven pairs", ""]
    for pair in PAIRS:
        verdict = report["observability"]["pairs"][pair.key]
        lines += [f"### {pair.key}", "", f"> “{pair.utah}”", ""]
        lines.append("Measurable in this run." if verdict["measurable"]
                     else f"Not measurable in this run. {verdict['gap']}")
        lines.append("")
        if pair.intervals:
            lines += _interval_table((_key(*i), report["intervals"][_key(*i)])
                                     for i in pair.intervals)
        elif pair.throughput:
            rates = report["throughput"]["pairs"][pair.key]
            lines += [f"- {name}: " + (NOT_MEASURED if rate is None else f"{rate} per second")
                      for name, rate in rates.items()]
        else:
            lines.append("See Faults for the before, during and after windows.")
        lines.append("")
    return lines


def _censored_md(incomplete: dict) -> list[str]:
    lines = [f"{incomplete['count']} credential(s) requested in the window never reached the "
             f"measure end; their latency is censored at the window's end.", ""]
    latency = incomplete["latency"]
    if latency.get("measured") is False:
        return lines + ["The censored latency is not measured: no sample could be placed on "
                        "the harness clock.", ""]
    for key in ("p50", "p90", "p99", "p99.9", "max"):
        quoted = latency[key]
        mark = " (lower bound)" if quoted["lower_bound"] else ""
        lines.append(f"- {key}: {_ns(quoted['value'])}{mark}")
    return lines + [""]


def _faults_md(faults: list[dict]) -> list[str]:
    lines = ["## Faults", ""]
    if not faults:
        return lines + ["No faults were scheduled in this run.", ""]
    for fault in faults:
        ended = ("not before the window closed" if fault["ended_ns"] is None
                 else _ns(fault["ended_ns"]))
        lines += [f"### {fault['kind']} on {fault['target']}", "",
                  f"Started: {_ns(fault['started_ns'])}; ended: {ended}.", ""]
        if "time_to_ready_ns" in fault:
            lines += [f"- time to ready: {_ns(fault['time_to_ready_ns'])}",
                      f"- time to first success: {_ns(fault['time_to_first_success_ns'])}", ""]
        lines += _interval_table((phase, fault[phase]) for phase in ("before", "during",
                                                                     "after"))
        lines.append("")
    return lines


def render_markdown(report: dict) -> str:
    run, window, driver = report["run"], report["window"], report["driver"]
    lines = ["# keri-bench report", "",
             f"Report schema {report['report_schema']}, generated by "
             f"{report['generator_commit']} from a timeline of schema "
             f"{report['timeline_schema']} (vocabulary {report['vocabulary_version']}) written "
             f"by harness {report['harness_commit']}. Scenario sha256 "
             f"{report['scenario_sha256']}.", "",
             f"Offered load {run['rate']} per second, repetition {run['repetition']}. The "
             f"window ran from {_ns(window['start_ns'])} to {_ns(window['end_ns'])}, measured "
             f"from {_ns(window['measured_from_ns'])} after {_ns(window['warmup_ns'])} of "
             f"warmup, and covers {window['credentials']} credential(s).", ""]
    if report["refused"]:
        lines += [f"This run is refused. {report['refused_reason']}", ""]
    lines += ["## Driver check", ""]
    lines.append(("Driver check passed" if driver["ok"] else "Driver check failed")
                 + f": p99 send lag {_ns(driver['p99_lag_ns'])} against a limit of "
                 f"{_ns(driver['lag_limit_ns'])}; backlog per second {driver['backlog']}.")
    lines.append("")
    lines += _pairs_md(report)
    lines += [f"## Measured interval ({report['measure']})", ""]
    lines += _interval_table([(report["measure"], report["intervals"][report["measure"]])])
    lines.append("")
    lines += _censored_md(report["incomplete"])
    lines += ["## All intervals", ""]
    lines += _interval_table(report["intervals"].items())
    lines += ["", "## Throughput", "",
              f"Over {_ns(report['throughput']['window_ns'])} after warmup, offered "
              f"{report['throughput']['offered_per_s']} per second.", "",
              "| milestone | count | per second | unconvertible |", "|---|---|---|---|"]
    for name, rate in report["throughput"]["milestones"].items():
        lines.append(f"| {name} | {rate['count']} | {rate['per_s']} | "
                     f"{rate['unconvertible']} |")
    lines += ["", "## Observability matrix", "",
              "| milestone | adapter | role | method | clock | resolution | events |",
              "|---|---|---|---|---|---|---|"]
    for name, seen in report["observability"]["milestones"].items():
        if seen == "unobserved":
            lines.append(f"| {name} | unobserved | | | | | |")
        else:
            lines.append(f"| {name} | {seen['adapter']} | {seen['role']} | {seen['method']} "
                         f"| {seen['clock']} | {_ns(seen['resolution_ns'])} | "
                         f"{seen['events']} |")
    lines.append("")
    lines += _faults_md(report["faults"])
    lines += ["## Reconciliation", ""]
    if report["reconciliation"] == NOT_MEASURED:
        lines.append("Reconciliation was not measured in this run.")
    else:
        lines += [f"- {k}: {v}" for k, v in report["reconciliation"]["summary"].items()]
    lines += ["", "## Measurement cost", ""]
    cost = report["measurement_cost"]
    if cost == NOT_MEASURED:
        lines.append("Measurement cost was not measured in this run.")
    else:
        lines += [f"- {link}: p50 {_ns(v['p50_ns'])}, p99 {_ns(v['p99_ns'])}"
                  for link, v in cost["links"].items()]
        lines.append(f"- harness CPU: {cost['harness_cpu_s']} s")
    if "calibration" in report:
        lines += ["", "## Calibration (error budget)", ""]
        calibration = report["calibration"]
        if isinstance(calibration, dict):
            lines.append(f"Calibrated by harness {calibration['harness_commit']}; "
                         f"{'passed' if calibration['ok'] else 'FAILED'}. The worst error per "
                         f"injected transition:")
            lines += [f"- {key}: p50 {_ns(v['p50_error_ns'])}, p99 {_ns(v['p99_error_ns'])}"
                      for key, v in calibration["budget"].items()]
        else:
            lines.append("This run carries no calibration.")
    lines += ["", "## Signer census", "",
              "The signer census is not measured until the external-signer interface exists "
              "(plan item heti-signer).", "", "## Warnings", ""]
    lines += [f"- {w}" for w in report["warnings"]] or ["No warnings."]
    return "\n".join(lines) + "\n"


def write_report(run_dir: Path, *, generator_commit: str) -> dict:
    run_dir = Path(run_dir)
    report = derive(run_dir / "timeline.jsonl", run_dir / "provenance.json",
                    generator_commit=generator_commit)
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (run_dir / "report.md").write_text(render_markdown(report))
    return report
