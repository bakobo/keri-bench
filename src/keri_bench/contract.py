"""The adapter contract suite (test plan, Part B #10).

An adapter passes when its class is a well-formed adapter for its role, and when a short run with
it in that role shows that it emitted every milestone it declares, only on the method, clock and
resolution it declares, that every endpoint it announced was reached through the harness's proxy,
and that no lifecycle stage failed. The suite runs against the synthetic adapters on every change;
a scheduled job runs it against each real adapter on its pinned stack versions, where a stack
that has drifted (stopped reporting a milestone, changed a clock) fails it.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from . import provenance
from .adapters.base import HARNESS, REALTIME, ROLE_BASES, Adapter
from .run import run_once
from .scenario import Scenario
from .timeline import read_timeline


def check_class(cls: type[Adapter]) -> list[str]:
    """Problems with an adapter class that need no run to find."""
    problems = []
    declaration = getattr(cls, "declaration", None)
    if declaration is None:
        return [f"{cls.__name__} has no declaration."]
    base = ROLE_BASES[declaration.role]
    if not issubclass(cls, base):
        problems.append(f"{declaration.adapter} declares the {declaration.role.value} role but "
                        f"is not a {base.__name__}.")
    if not declaration.tested_against:
        problems.append(f"{declaration.adapter} names no stack version it was contract-tested "
                        f"against, so a report cannot warn when the stack moves.")
    return problems


def _kind(clock: str, harness: str, realtime: str) -> str:
    return {harness: HARNESS, realtime: REALTIME}.get(clock, clock)


def check_run(run_dir: Path) -> dict[str, list[str]]:
    """Problems per adapter, read from a finished run's timeline and provenance."""
    run_dir = Path(run_dir)
    header, events = read_timeline(run_dir / "timeline.jsonl")
    record = provenance.read(run_dir / "provenance.json")
    harness, realtime = record["clocks"]["harness"], record["clocks"]["realtime"]
    emitted: dict[str, set[str]] = defaultdict(set)
    problems: dict[str, list[str]] = defaultdict(list)
    declared = {d["adapter"]: d for d in record["adapters"].values()}
    for event in events:
        adapter = (event.attrs or {}).get("adapter")
        if adapter is None:
            continue
        emitted[adapter].add(event.name)
        seen = declared.get(adapter, {}).get("milestones", {}).get(event.name)
        if seen is None:
            problems[adapter].append(f"emitted {event.name}, which it does not declare")
            continue
        got = (event.method, _kind(event.clock, harness, realtime), event.resolution)
        want = (seen["method"], seen["clock"], seen["resolution_ns"])
        if got != want:
            problems[adapter].append(f"emitted {event.name} as {got}, but declares {want}")
    offsets = {(e.attrs or {}).get("domain") for e in events if e.name == "clock_offset"}
    for role, declaration in record["adapters"].items():
        adapter = declaration["adapter"]
        problems.setdefault(adapter, [])
        clocks = {m["clock"] for m in declaration["milestones"].values()}
        for clock in sorted(c for c in clocks if c.startswith("stack:") and c not in offsets):
            problems[adapter].append(f"declares the stack clock {clock} but supplied no reader, "
                                     f"so its offset was never measured")
        for name in sorted(set(declaration["milestones"]) - emitted[adapter]):
            problems[adapter].append(f"declares {name} but never emitted it")
    for link, cost in record["measurement_cost"]["links"].items():
        if not cost.get("exchanges"):
            role = link.split(".")[0]
            problems[record["adapters"][role]["adapter"]].append(
                f"announced the endpoint {link}, but nothing reached it through the proxy")
    failures = [e for e in events if e.name == "stage_failed"]
    if failures:
        problems["harness"].append(f"{len(failures)} lifecycle stage(s) failed, the first: "
                                   f"{failures[0].attrs}")
    return dict(problems)


async def contract(scenario: Scenario, out: Path, *, harness_commit: str) -> dict:
    """Run the scenario once at its first rate and check every adapter in it."""
    from .run import plan as resolve

    classes = resolve(scenario).classes
    static = {cls.declaration.adapter: check_class(cls) for cls in classes.values()}
    await run_once(scenario, out, rate=scenario.load.rates[0], repetition=1,
                   harness_commit=harness_commit)
    dynamic = check_run(Path(out) / f"rate-{scenario.load.rates[0]:g}" / "rep-1")
    adapters = sorted(set(static) | set(dynamic))
    result = {name: static.get(name, []) + dynamic.get(name, []) for name in adapters}
    return {"ok": not any(result.values()), "problems": result}
