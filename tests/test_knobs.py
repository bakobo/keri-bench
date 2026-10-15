"""Versioned knob schemas, and refusing any knob a scenario leaves unset (this.i 4q4540c4)."""

import pytest

from keri_bench.errors import BenchError
from keri_bench.knobs import NA, Knob, KnobSchema, resolve

SCHEMA = KnobSchema(version=2, knobs={
    "window_s": Knob(float, "Registrar batching window", unit="s"),
    "toad": Knob(int, "Receipts required"),
    "channel": Knob(str, "Delivery channel"),
    "gap_grace_s": Knob(float, "Observer gap grace", allow_na=True, unit="s"),
    "strict": Knob(bool, "Refuse on stale state"),
})
FULL = {"window_s": 900, "toad": 2, "channel": "relay", "gap_grace_s": NA, "strict": True,
        "knob_schema": 2}


def test_a_complete_scenario_resolves_with_ints_widened_to_floats():
    resolved = resolve(SCHEMA, FULL, owner="issuer")
    assert resolved == {"window_s": 900.0, "toad": 2, "channel": "relay", "gap_grace_s": NA,
                        "strict": True}
    assert isinstance(resolved["window_s"], float)


@pytest.mark.parametrize("change, code", [
    ({"toad": None}, "e.input.missing.scenario.knob.f"),
    ({"window_s": NA}, "e.input.range.scenario.knob.na.f"),
    ({"toad": 2.5}, "e.input.format.scenario.knob.f"),
    ({"toad": True}, "e.input.format.scenario.knob.f"),
    ({"window_s": "fast"}, "e.input.format.scenario.knob.f"),
    ({"strict": 1}, "e.input.format.scenario.knob.f"),
    ({"surprise": 1}, "e.input.range.scenario.knob.unknown.f"),
    ({"knob_schema": None}, "e.input.missing.scenario.knob-schema.f"),
    ({"knob_schema": 1}, "e.state.conflict.scenario.knob-schema.f"),
])
def test_an_incomplete_or_wrong_knob_set_is_refused_with_a_code(change, code):
    given = dict(FULL)
    for key, value in change.items():
        if value is None:
            given.pop(key)
        else:
            given[key] = value
    with pytest.raises(BenchError) as refused:
        resolve(SCHEMA, given, owner="issuer")
    assert refused.value.code == code
    assert "issuer" in refused.value.message


def test_a_schema_rejects_an_unsupported_knob_type():
    with pytest.raises(BenchError) as refused:
        KnobSchema(version=1, knobs={"x": Knob(list, "no")})
    assert refused.value.code == "e.input.range.knob.type.f"


def test_a_schema_needs_a_positive_version():
    with pytest.raises(BenchError):
        KnobSchema(version=0, knobs={})


def test_a_schema_describes_itself_for_provenance():
    assert SCHEMA.describe() == {"version": 2, "knobs": {
        "window_s": {"type": "float", "description": "Registrar batching window", "unit": "s",
                     "allow_na": False},
        "toad": {"type": "int", "description": "Receipts required", "unit": "", "allow_na": False},
        "channel": {"type": "str", "description": "Delivery channel", "unit": "",
                    "allow_na": False},
        "gap_grace_s": {"type": "float", "description": "Observer gap grace", "unit": "s",
                        "allow_na": True},
        "strict": {"type": "bool", "description": "Refuse on stale state", "unit": "",
                   "allow_na": False},
    }}
