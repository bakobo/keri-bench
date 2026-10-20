"""Scenario files: every policy knob set, every number recorded (this.i 4q4540c4; Part B #6)."""

import pytest

from keri_bench.errors import BenchError
from keri_bench.scenario import SCENARIO_SCHEMA, Scenario, load_scenario

GOOD = """
scenario_schema = 1
name = "issue-steady"
description = "Steady issuance."
stages = ["issue", "deliver", "present", "verify", "revoke"]

[recognition]
poll_ms = 20.0
stable_polls = 3

[load]
rates = [10, 20, 40]
duration_s = 5.0
warmup_s = 1.0
drain_s = 10.0
repetitions = 3
measure = ["requested", "registry_updated"]

[statistics]
epsilon = 0.05

[roles.issuer]
adapter = "synthetic.issuer"
[roles.issuer.knobs]
knob_schema = 1
service_ms = 5

[roles.verifier]
adapter = "synthetic.verifier"
[roles.verifier.knobs]
knob_schema = 1

[[faults]]
kind = "crash"
target = "issuer"
at_s = 2.0

[[faults]]
kind = "delay"
target = "issuer.control"
at_s = 1.0
duration_s = 1.0
delay_ms = 20.0
"""


def write(tmp_path, text):
    path = tmp_path / "scenario.toml"
    path.write_text(text)
    return path


def test_a_complete_scenario_loads_with_plan_defaults_for_the_statistics(tmp_path):
    scenario = load_scenario(write(tmp_path, GOOD))
    assert isinstance(scenario, Scenario)
    assert scenario.name == "issue-steady"
    assert scenario.load.rates == (10.0, 20.0, 40.0)
    assert scenario.load.repetitions == 3
    assert scenario.load.measure == ("requested", "registry_updated")
    assert scenario.statistics.epsilon == 0.05
    assert scenario.statistics.k == 5.0 and scenario.statistics.lag_limit_ms == 5.0
    assert scenario.roles["issuer"].adapter == "synthetic.issuer"
    assert scenario.roles["issuer"].knobs == {"knob_schema": 1, "service_ms": 5}
    crash, delay = scenario.faults
    assert (crash.kind, crash.target, crash.at_s, crash.duration_s) == ("crash", "issuer", 2.0, 0.0)
    assert (delay.delay_ms, delay.duration_s) == (20.0, 1.0)
    assert scenario.stages == ("issue", "deliver", "present", "verify", "revoke")
    assert (scenario.recognition.poll_ms, scenario.recognition.stable_polls) == (20.0, 3)
    assert len(scenario.sha256) == 64
    assert scenario.text == GOOD
    assert SCENARIO_SCHEMA == 1


def replace(old, new):
    assert old in GOOD
    return GOOD.replace(old, new)


@pytest.mark.parametrize("text, code", [
    ("not = [toml", "e.input.format.scenario.toml.f"),
    (replace("scenario_schema = 1", "scenario_schema = 9"),
     "e.feature.unsupported.scenario.schema.f"),
    (replace("scenario_schema = 1", ""), "e.input.missing.scenario.field.f"),
    (replace('name = "issue-steady"', ""), "e.input.missing.scenario.field.f"),
    (replace("rates = [10, 20, 40]", "rates = []"), "e.input.range.scenario.load.f"),
    (replace("rates = [10, 20, 40]", "rates = [10, 5]"), "e.input.range.scenario.load.f"),
    (replace("rates = [10, 20, 40]", "rates = [0, 5]"), "e.input.range.scenario.load.f"),
    (replace("repetitions = 3", "repetitions = 2"), "e.input.range.scenario.load.f"),
    (replace("duration_s = 5.0\nwarmup", "duration_s = -1\nwarmup"),
     "e.input.range.scenario.load.f"),
    (replace('measure = ["requested", "registry_updated"]', 'measure = ["requested"]'),
     "e.input.format.scenario.load.f"),
    (replace('measure = ["requested", "registry_updated"]', 'measure = ["a", "b"]'),
     "e.input.range.scenario.load.f"),
    (replace("[statistics]\nepsilon = 0.05", "[statistics]\nepsilon = -1"),
     "e.input.range.scenario.statistics.f"),
    (replace("[statistics]\nepsilon = 0.05", "[statistics]\nwhimsy = 1"),
     "e.input.range.scenario.field.f"),
    (replace('adapter = "synthetic.issuer"', ""), "e.input.missing.scenario.field.f"),
    (replace("[roles.issuer]", "[roles.bystander]").replace("[roles.issuer.knobs]",
                                                           "[roles.bystander.knobs]"),
     "e.input.range.scenario.role.f"),
    (replace('stages = ["issue", "deliver", "present", "verify", "revoke"]',
             'stages = ["deliver"]'), "e.input.range.scenario.stages.f"),
    (replace('stages = ["issue", "deliver", "present", "verify", "revoke"]',
             'stages = ["issue", "dance"]'), "e.input.range.scenario.stages.f"),
    (replace('kind = "crash"', 'kind = "flood"'), "e.input.range.scenario.fault.f"),
    (replace("at_s = 2.0", "at_s = -2.0"), "e.input.range.scenario.fault.f"),
    (replace("delay_ms = 20.0", ""), "e.input.missing.scenario.fault.f"),
    (replace("delay_ms = 20.0", "delay_ms = -1.0"), "e.input.range.scenario.fault.f"),
    (replace("at_s = 1.0\nduration_s = 1.0", "at_s = 1.0\nduration_s = inf"),
     "e.input.range.scenario.fault.f"),
    (replace("rates = [10, 20, 40]", "rates = [10, inf]"), "e.input.range.scenario.load.f"),
    (replace("at_s = 1.0\nduration_s = 1.0", "at_s = 1.0"), "e.input.missing.scenario.fault.f"),
    (replace("[recognition]\npoll_ms = 20.0\nstable_polls = 3\n", ""),
     "e.input.missing.scenario.field.f"),
    (replace("stable_polls = 3", "stable_polls = 0"), "e.input.range.scenario.recognition.f"),
    (replace("poll_ms = 20.0", "poll_ms = -1"), "e.input.range.scenario.recognition.f"),
    (replace("[roles.issuer.knobs]\nknob_schema = 1\nservice_ms = 5", ""),
     "e.input.missing.scenario.field.f"),
    (replace('target = "issuer"\nat_s = 2.0', 'target = ""\nat_s = 2.0'),
     "e.input.missing.scenario.fault.f"),
])
def test_a_bad_scenario_is_refused_with_a_code(tmp_path, text, code):
    with pytest.raises(BenchError) as refused:
        load_scenario(write(tmp_path, text))
    assert refused.value.code == code


def test_an_oversized_scenario_is_refused_before_it_is_parsed(tmp_path, monkeypatch):
    import keri_bench.scenario as scenario
    monkeypatch.setattr(scenario, "MAX_BYTES", 10)
    with pytest.raises(BenchError) as refused:
        load_scenario(write(tmp_path, GOOD))
    assert refused.value.code == "e.input.range.scenario.size.f"


def test_a_soak_stop_rule_is_read_when_present(tmp_path):
    scenario = load_scenario(write(tmp_path, GOOD + "\n[stop]\ncredentials = 1000\n"))
    assert scenario.stop.credentials == 1000
    assert load_scenario(write(tmp_path, GOOD)).stop is None


SOAK = "\n[soak]\nbucket = 50\nper_credentials = 1000\nsample_s = 5.0\n"
PRESEED = "\n[preseed]\ncredentials = 200\nconcurrency = 4\n"


def test_soak_and_preseed_tables_are_read_when_present(tmp_path):
    scenario = load_scenario(write(tmp_path, GOOD + SOAK + PRESEED))
    assert (scenario.soak.bucket, scenario.soak.per_credentials, scenario.soak.sample_s) == \
        (50, 1000, 5.0)
    assert (scenario.preseed.credentials, scenario.preseed.concurrency) == (200, 4)
    plain = load_scenario(write(tmp_path, GOOD))
    assert plain.soak is None and plain.preseed is None


@pytest.mark.parametrize("extra, code", [
    ("\n[stop]\n", "e.input.missing.scenario.field.f"),
    ("\n[stop]\ncredentials = 0\n", "e.input.range.scenario.stop.f"),
    ("\n[stop]\ncredentials = 1.5\n", "e.input.range.scenario.stop.f"),
    ("\n[stop]\ncredentials = true\n", "e.input.range.scenario.stop.f"),
    ("\n[stop]\ncredentials = 9\nduration_s = 60.0\n", "e.input.range.scenario.field.f"),
    (SOAK.replace("bucket = 50", "bucket = 1"), "e.input.range.scenario.soak.f"),
    (SOAK.replace("bucket = 50", ""), "e.input.missing.scenario.field.f"),
    (SOAK.replace("per_credentials = 1000", "per_credentials = 0"),
     "e.input.range.scenario.soak.f"),
    (SOAK.replace("sample_s = 5.0", "sample_s = -1.0"), "e.input.range.scenario.soak.f"),
    (SOAK.replace("sample_s = 5.0", "sample_s = nan"), "e.input.range.scenario.soak.f"),
    (SOAK + "whimsy = 1\n", "e.input.range.scenario.field.f"),
    (PRESEED.replace("credentials = 200", "credentials = 0"),
     "e.input.range.scenario.preseed.f"),
    (PRESEED.replace("concurrency = 4", "concurrency = 2.5"),
     "e.input.range.scenario.preseed.f"),
    (PRESEED.replace("concurrency = 4", ""), "e.input.missing.scenario.field.f"),
    (PRESEED + "whimsy = 1\n", "e.input.range.scenario.field.f"),
])
def test_bad_stop_soak_or_preseed_tables_are_refused_with_a_code(tmp_path, extra, code):
    with pytest.raises(BenchError) as refused:
        load_scenario(write(tmp_path, GOOD + extra))
    assert refused.value.code == code


def test_a_scenario_that_never_revokes_needs_no_recognition_rule(tmp_path):
    text = replace('stages = ["issue", "deliver", "present", "verify", "revoke"]',
                   'stages = ["issue"]').replace("[recognition]\npoll_ms = 20.0\n"
                                                 "stable_polls = 3\n", "")
    assert load_scenario(write(tmp_path, text)).recognition is None
