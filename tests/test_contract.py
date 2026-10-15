"""The adapter contract suite (Part B #10), run against the synthetic adapters and on crafted
runs that break each clause."""

import json
from pathlib import Path

from keri_bench import contract
from keri_bench.adapters.base import Adapter, Declaration, Observation
from keri_bench.knobs import KnobSchema
from keri_bench.scenario import load_scenario
from keri_bench.synthetic.adapters import SyntheticIssuer
from keri_bench.timeline import Event, Header, TimelineWriter
from keri_bench.vocabulary import Role

SCENARIOS = Path(__file__).parent.parent / "scenarios"
H, W = "monotonic@b", "realtime@h"


async def test_every_synthetic_adapter_passes_its_contract(tmp_path):
    result = await contract.contract(load_scenario(SCENARIOS / "contract-synthetic.toml"),
                                     tmp_path, harness_commit="test")
    assert result["ok"], result["problems"]
    assert "synthetic.verifier" in result["problems"]


def test_a_class_that_is_not_its_roles_adapter_or_names_no_tested_version_fails():
    class Wrong(Adapter):
        declaration = Declaration(adapter="wrong", role=Role.HOLDER, version="1",
                                  milestones={}, knobs=KnobSchema(1, {}), tested_against={})

    class Bare:
        pass

    problems = contract.check_class(Wrong)
    assert any("is not a HolderAdapter" in p for p in problems)
    assert any("names no stack version" in p for p in problems)
    assert contract.check_class(Bare) == ["Bare has no declaration."]
    assert contract.check_class(SyntheticIssuer) == []


def crafted(tmp_path, events, exchanges=1):
    declaration = {"adapter": "x.issuer", "role": "issuer", "version": "1", "knobs": {},
                   "tested_against": {"x": "1"}, "milestones": {
                       "constructed": {"method": "observed", "clock": "harness",
                                       "resolution_ns": 1},
                       "anchored": {"method": "reported", "clock": "stack:x",
                                    "resolution_ns": 1000},
                       "witnessed": {"method": "reported", "clock": "realtime",
                                     "resolution_ns": 1000}}}
    record = {"provenance_schema": 1, "harness_commit": "t", "scenario": "", "scenario_sha256":
              "0", "adapters": {"issuer": declaration}, "knobs": {}, "components": {},
              "host": {}, "clocks": {"harness": H, "realtime": W}, "run": {},
              "measurement_cost": {"links": {"issuer.api": {"exchanges": exchanges}}}}
    (tmp_path / "provenance.json").write_text(json.dumps(record))
    with TimelineWriter(tmp_path / "timeline.jsonl", Header("t", H)) as sink:
        for event in events:
            sink.write(event)
    return tmp_path


def ev(name, method="observed", clock=H, resolution=1, adapter="x.issuer", role="issuer"):
    return Event(name=name, t=1, method=method, clock=clock, resolution=resolution,
                 credentials=("c1",), role=role, attrs={"adapter": adapter} if adapter else None)



def test_each_broken_clause_is_named(tmp_path):
    run = crafted(tmp_path, [
        ev("constructed", method="polled", resolution=5),       # wrong method and resolution
        ev("accepted"),                                         # undeclared
        ev("witnessed", method="reported", clock=W, resolution=1000, role="witness_pool"),
        ev("requested", adapter=None),                          # no adapter: the harness's own
        Event(name="stage_failed", t=2, method="observed", clock=H, resolution=1,
              credentials=("c1",), role="harness", attrs={"stage": "issue"}),
    ], exchanges=0)
    problems = contract.check_run(run)
    issuer = problems["x.issuer"]
    assert any("emitted constructed as ('polled', 'harness', 5)" in p for p in issuer)
    assert any("emitted accepted, which it does not declare" in p for p in issuer)
    assert any("declares anchored but never emitted it" in p for p in issuer)
    assert any("stack clock stack:x but supplied no reader" in p for p in issuer)
    assert any("issuer.api, but nothing reached it" in p for p in issuer)
    assert not any("witnessed" in p for p in issuer)
    assert problems["harness"][0].startswith("1 lifecycle stage(s) failed")


def test_a_run_meeting_every_clause_has_no_problems(tmp_path):
    offset = Event(name="clock_offset", t=0, method="observed", clock=H, resolution=1,
                   credentials=(), role="harness",
                   attrs={"domain": "stack:x", "offset_ns": 0, "bracket_ns": 1})
    run = crafted(tmp_path, [
        offset, ev("constructed"),
        ev("anchored", method="reported", clock="stack:x", resolution=1000),
        ev("witnessed", method="reported", clock=W, resolution=1000, role="witness_pool")])
    assert contract.check_run(run) == {"x.issuer": []}
