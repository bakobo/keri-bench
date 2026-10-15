"""Policy knobs: each adapter declares a versioned schema, and a scenario must set every knob.

An unset knob is an error, never a default, so no report rests on a value nobody chose
(this.i 4q4540c4). ``n/a`` is accepted only where the adapter declares the knob may not apply.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .errors import BenchError

NA = "n/a"

E_MISSING = "e.input.missing.scenario.knob.f"
E_NA = "e.input.range.scenario.knob.na.f"
E_TYPE = "e.input.format.scenario.knob.f"
E_UNKNOWN = "e.input.range.scenario.knob.unknown.f"
E_SCHEMA_MISSING = "e.input.missing.scenario.knob-schema.f"
E_SCHEMA_CONFLICT = "e.state.conflict.scenario.knob-schema.f"
E_KNOB_TYPE = "e.input.range.knob.type.f"
E_SCHEMA_VERSION = "e.input.range.knob.schema-version.f"

_TYPES = (int, float, str, bool)


@dataclass(frozen=True)
class Knob:
    kind: type
    description: str
    allow_na: bool = False
    unit: str = ""


@dataclass(frozen=True)
class KnobSchema:
    version: int
    knobs: Mapping[str, Knob]

    def __post_init__(self) -> None:
        if not isinstance(self.version, int) or self.version < 1:
            raise BenchError(E_SCHEMA_VERSION, f"A knob schema's version must be a positive "
                             f"integer, not {self.version!r}.")
        for name, knob in self.knobs.items():
            if knob.kind not in _TYPES:
                raise BenchError(E_KNOB_TYPE, f"The knob {name!r} has type {knob.kind!r}; "
                                 f"knobs are int, float, str or bool.")

    def describe(self) -> dict:
        return {"version": self.version, "knobs": {
            name: {"type": knob.kind.__name__, "description": knob.description,
                   "unit": knob.unit, "allow_na": knob.allow_na}
            for name, knob in self.knobs.items()}}


def _typed(knob: Knob, value: Any) -> bool:
    if isinstance(value, bool):
        return knob.kind is bool
    if knob.kind is float:
        return isinstance(value, (int, float))
    return isinstance(value, knob.kind)


def resolve(schema: KnobSchema, given: Mapping[str, Any], *, owner: str) -> dict[str, Any]:
    """Check a scenario's knobs for one adapter against its schema and return them resolved.

    ``given`` carries ``knob_schema``, the schema version the scenario was written against, so
    a scenario written for an older schema is refused rather than silently reinterpreted.
    """
    if "knob_schema" not in given:
        raise BenchError(E_SCHEMA_MISSING, f"The scenario does not say which knob schema version "
                         f"it sets for {owner}; that adapter's schema is version "
                         f"{schema.version}.")
    if given["knob_schema"] != schema.version:
        raise BenchError(E_SCHEMA_CONFLICT, f"The scenario sets {owner}'s knobs against schema "
                         f"version {given['knob_schema']!r}, but the adapter's schema is version "
                         f"{schema.version}. Update the scenario rather than reusing it.")
    values = {k: v for k, v in given.items() if k != "knob_schema"}
    unknown = sorted(set(values) - set(schema.knobs))
    if unknown:
        raise BenchError(E_UNKNOWN, f"The scenario sets {unknown} for {owner}, which its "
                         f"adapter does not declare.")
    resolved = {}
    for name, knob in schema.knobs.items():
        if name not in values:
            raise BenchError(E_MISSING, f"The scenario leaves {owner}'s knob {name!r} unset. "
                             f"Every knob must be set, or set to {NA!r} where allowed.")
        value = values[name]
        if value == NA:
            if not knob.allow_na:
                raise BenchError(E_NA, f"{owner}'s knob {name!r} always applies, so it cannot "
                                 f"be {NA!r}.")
            resolved[name] = NA
            continue
        if not _typed(knob, value):
            raise BenchError(E_TYPE, f"{owner}'s knob {name!r} must be a "
                             f"{knob.kind.__name__}, not {value!r}.")
        resolved[name] = float(value) if knob.kind is float else value
    return resolved
