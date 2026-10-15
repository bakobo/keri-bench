"""Scenario files (TOML): the load, the statistics, the adapters per role, their knobs and the
scheduled faults.

Knob values are checked against each adapter's schema when the run resolves its adapters
(``knobs.resolve``); this module checks everything a scenario says about the run itself. The
scenario's text and hash go into provenance verbatim, so a report names exactly what it ran.
"""

from __future__ import annotations

import hashlib
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import BenchError
from .vocabulary import MILESTONES, Role

SCENARIO_SCHEMA = 1
MAX_BYTES = 1 << 20

STAGES = ("issue", "deliver", "present", "verify", "revoke", "sync")
"""What happens to each credential, in this order; a scenario names a subset that starts with
issue. ``sync`` is the exception: once the drain ends, a cold verifier synchronizes once against
everything issued, which is pair 5's initial synchronization."""

FAULT_KINDS = ("crash", "partition", "delay", "duplicate")

E_SIZE = "e.input.range.scenario.size.f"
E_TOML = "e.input.format.scenario.toml.f"
E_SCHEMA = "e.feature.unsupported.scenario.schema.f"
E_FIELD = "e.input.missing.scenario.field.f"
E_UNKNOWN_FIELD = "e.input.range.scenario.field.f"
E_LOAD = "e.input.range.scenario.load.f"
E_LOAD_FORMAT = "e.input.format.scenario.load.f"
E_STATISTICS = "e.input.range.scenario.statistics.f"
E_ROLE = "e.input.range.scenario.role.f"
E_STAGES = "e.input.range.scenario.stages.f"
E_FAULT = "e.input.range.scenario.fault.f"
E_FAULT_MISSING = "e.input.missing.scenario.fault.f"
E_RECOGNITION = "e.input.range.scenario.recognition.f"


@dataclass(frozen=True)
class Load:
    rates: tuple[float, ...]
    """Offered issuance requests per second, one step each, increasing."""
    duration_s: float
    warmup_s: float
    drain_s: float
    repetitions: int
    measure: tuple[str, str]
    """The interval whose p99 and whose end-milestone completion rate decide saturation."""


@dataclass(frozen=True)
class Statistics:
    epsilon: float = 0.02
    k: float = 5.0
    lag_limit_ms: float = 5.0


@dataclass(frozen=True)
class RoleConfig:
    adapter: str
    knobs: Mapping[str, Any]


@dataclass(frozen=True)
class Fault:
    kind: str
    target: str
    """A role for ``crash``; a link name (``<role>.<endpoint>``) for the others."""
    at_s: float
    duration_s: float = 0.0
    delay_ms: float = 0.0


@dataclass(frozen=True)
class Recognition:
    """How the harness decides a verifier reliably recognises a revocation (test plan,
    Milestones: revocation_recognised). Part of the run's identity, so it is never defaulted."""

    poll_ms: float
    stable_polls: int


@dataclass(frozen=True)
class Stop:
    credentials: int | None
    duration_s: float | None


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    stages: tuple[str, ...]
    load: Load
    statistics: Statistics
    roles: Mapping[str, RoleConfig]
    faults: tuple[Fault, ...]
    stop: Stop | None
    recognition: Recognition | None
    text: str
    sha256: str


def _need(table: Mapping, key: str, where: str) -> Any:
    if key not in table:
        raise BenchError(E_FIELD, f"The scenario's {where} has no {key!r}, and nothing in a "
                         f"scenario has a default.")
    return table[key]


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _load(table: Mapping) -> Load:
    rates = _need(table, "rates", "[load]")
    duration = _need(table, "duration_s", "[load]")
    warmup = _need(table, "warmup_s", "[load]")
    drain = _need(table, "drain_s", "[load]")
    repetitions = _need(table, "repetitions", "[load]")
    measure = _need(table, "measure", "[load]")
    if not isinstance(rates, list) or not rates or not all(_number(r) and r > 0 for r in rates) \
            or any(b <= a for a, b in zip(rates, rates[1:])):
        raise BenchError(E_LOAD, "[load] rates must be one or more positive, strictly "
                         "increasing request rates.")
    if not all(_number(v) and v >= 0 for v in (duration, warmup, drain)) or duration <= 0:
        raise BenchError(E_LOAD, "[load] duration_s must be positive, and warmup_s and drain_s "
                         "must not be negative.")
    if not isinstance(repetitions, int) or repetitions < 3:
        raise BenchError(E_LOAD, "[load] repetitions must be at least 3, so every reported "
                         "point has a spread (test plan, Statistics).")
    if not isinstance(measure, list) or len(measure) != 2:
        raise BenchError(E_LOAD_FORMAT, "[load] measure must name two milestones: the interval "
                         "that decides saturation.")
    if not all(name in MILESTONES for name in measure):
        raise BenchError(E_LOAD, f"[load] measure names {measure}, which are not all "
                         f"milestones.")
    return Load(tuple(float(r) for r in rates), float(duration), float(warmup), float(drain),
                repetitions, (measure[0], measure[1]))


def _statistics(table: Mapping) -> Statistics:
    unknown = set(table) - {"epsilon", "k", "lag_limit_ms"}
    if unknown:
        raise BenchError(E_UNKNOWN_FIELD, f"[statistics] has {sorted(unknown)}, which the "
                         f"harness does not read.")
    stats = Statistics(**table)
    if not all(_number(v) and v > 0 for v in (stats.epsilon, stats.k, stats.lag_limit_ms)):
        raise BenchError(E_STATISTICS, "[statistics] epsilon, k and lag_limit_ms must be "
                         "positive numbers.")
    return stats


def _roles(table: Mapping) -> dict[str, RoleConfig]:
    roles = {}
    for role, config in table.items():
        if role not in {r.value for r in Role}:
            raise BenchError(E_ROLE, f"The scenario configures {role!r}, which is not a role. "
                             f"The roles are {[r.value for r in Role]}.")
        roles[role] = RoleConfig(_need(config, "adapter", f"[roles.{role}]"),
                                 dict(_need(config, "knobs", f"[roles.{role}]")))
    return roles


def _stages(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or value[0] != "issue" \
            or any(stage not in STAGES for stage in value):
        raise BenchError(E_STAGES, f"stages must start with 'issue' and use only {STAGES}.")
    return tuple(value)


def _fault(table: Mapping) -> Fault:
    kind = _need(table, "kind", "[[faults]]")
    target = table.get("target")
    at = _need(table, "at_s", "[[faults]]")
    if kind not in FAULT_KINDS:
        raise BenchError(E_FAULT, f"A fault's kind is {kind!r}; it must be one of {FAULT_KINDS}.")
    if not target:
        raise BenchError(E_FAULT_MISSING, f"The {kind} fault names no target.")
    if not _number(at) or at < 0:
        raise BenchError(E_FAULT, f"The {kind} fault's at_s must be a time from the start of "
                         f"the measured window, not {at!r}.")
    duration = table.get("duration_s", 0.0 if kind == "crash" else None)
    if duration is None:
        raise BenchError(E_FAULT_MISSING, f"The {kind} fault on {target} needs duration_s.")
    delay = table.get("delay_ms", 0.0 if kind != "delay" else None)
    if delay is None:
        raise BenchError(E_FAULT_MISSING, f"The delay fault on {target} needs delay_ms.")
    return Fault(kind, target, float(at), float(duration), float(delay))


def _recognition(table: Mapping | None, stages: tuple[str, ...]) -> Recognition | None:
    if "revoke" not in stages:
        return None
    if table is None:
        raise BenchError(E_FIELD, "The scenario revokes credentials but has no [recognition] "
                         "table saying how often to poll and how many polls make it reliable.")
    poll = _need(table, "poll_ms", "[recognition]")
    stable = _need(table, "stable_polls", "[recognition]")
    if not (_number(poll) and poll > 0 and isinstance(stable, int) and stable >= 1):
        raise BenchError(E_RECOGNITION, "[recognition] poll_ms must be positive and "
                         "stable_polls at least 1.")
    return Recognition(float(poll), stable)


def load_scenario(path: Path) -> Scenario:
    raw = Path(path).read_bytes()
    if len(raw) > MAX_BYTES:
        raise BenchError(E_SIZE, f"{path} is larger than {MAX_BYTES} bytes, so it was not read.")
    text = raw.decode()
    try:
        table = tomllib.loads(text)
    except tomllib.TOMLDecodeError as wrong:
        raise BenchError(E_TOML, f"{path} is not valid TOML ({wrong}).") from None
    schema = _need(table, "scenario_schema", "top level")
    if schema != SCENARIO_SCHEMA:
        raise BenchError(E_SCHEMA, f"{path} uses scenario schema {schema!r}; this harness reads "
                         f"schema {SCENARIO_SCHEMA}.")
    stop = table.get("stop")
    stages = _stages(_need(table, "stages", "top level"))
    return Scenario(
        name=_need(table, "name", "top level"), description=table.get("description", ""),
        stages=stages, recognition=_recognition(table.get("recognition"), stages),
        load=_load(_need(table, "load", "top level")),
        statistics=_statistics(table.get("statistics", {})),
        roles=_roles(_need(table, "roles", "top level")),
        faults=tuple(_fault(f) for f in table.get("faults", [])),
        stop=Stop(stop.get("credentials"), stop.get("duration_s")) if stop else None,
        text=text, sha256=hashlib.sha256(raw).hexdigest())
