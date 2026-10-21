"""Timeline to report (test plan, Outputs, Statistics, Observability matrix, Faults; Part B #2).

The unit tests build tiny runs in a temporary directory. The golden tests at the bottom read the
hand-written timelines under ``tests/golden/v<schema>/<name>/`` and check the report against
values computed by hand in this file, never by the code under test.
"""

import json
from pathlib import Path

import pytest

from keri_bench import provenance, report
from keri_bench.errors import BenchError
from keri_bench.timeline import Event, Header, TimelineWriter
from keri_bench.vocabulary import MILESTONES, PAIRS

H = "monotonic@boot-t"
SK = "stack:imbu"
MS = 1_000_000


def ev(name, t, creds=(), clock=H, method=None, resolution=1, attrs=None, batch_id=None):
    if method is None:
        method = "reported" if clock != H else "observed"
    return Event(name=name, t=t, method=method, clock=clock, resolution=resolution,
                 credentials=tuple(creds), role=MILESTONES.get(name, "harness").__str__(),
                 batch_id=batch_id, attrs=attrs)


def decl(adapter, role, milestones, tested="1"):
    return {"adapter": adapter, "role": role, "version": "1",
            "milestones": {name: {"method": m, "clock": c, "resolution_ns": r}
                           for name, (m, c, r) in milestones.items()},
            "knobs": {"version": 1, "knobs": {}}, "tested_against": {"stack": tested}}


ADAPTERS = {
    "issuer": decl("fake.issuer", "issuer", {
        "constructed": ("observed", "harness", 1),
        "registry_updated": ("observed", "harness", 1),
        "anchored": ("reported", SK, 1000),
        "witnessed": ("reported", SK, 1000)}),
    "verifier": decl("fake.verifier", "verifier", {"verified": ("polled", "harness", 500)}),
}


def run_section(**over):
    base = {"rate": 1.0, "repetition": 1, "warmup_s": 0.0, "lag_limit_ms": 5.0,
            "measure": ["requested", "verified"], "stages": ["steady"]}
    base.update(over)
    return base


def write_run(where: Path, events, *, run=None, adapters=None, components=None, **extra):
    where.mkdir(parents=True, exist_ok=True)
    with TimelineWriter(where / "timeline.jsonl", Header("hc", H)) as out:
        for event in events:
            out.write(event)
    provenance.write(where / "provenance.json", provenance.build(
        harness_commit="hc", scenario_text="", scenario_sha256="ab" * 32,
        adapters=ADAPTERS if adapters is None else adapters, knobs={},
        components={"issuer": {"stack": "1"}} if components is None else components,
        host={}, clocks={"harness": H}, run=run or run_section(), **extra))
    return where


def derive(where: Path):
    return report.derive(where / "timeline.jsonl", where / "provenance.json",
                         generator_commit="gc")


def window(end=10_000):
    return [ev("run_started", 0), ev("run_ended", end)]


def simple(c, t0, verified_after, dispatched_lag=0):
    return [ev("requested", t0, [c]), ev("dispatched", t0 + dispatched_lag, [c]),
            ev("verified", t0 + verified_after, [c], method="polled", resolution=500)]


def refused_code(where):
    with pytest.raises(BenchError) as refused:
        derive(where)
    return refused.value.code


# --- refusals ---------------------------------------------------------------------------------

def test_a_timeline_without_its_window_is_refused(tmp_path):
    where = write_run(tmp_path, simple("c1", 100, 1000))
    assert refused_code(where) == "e.input.missing.timeline.window.f"


def test_a_run_section_lacking_a_field_is_refused(tmp_path):
    run = run_section()
    del run["lag_limit_ms"]
    where = write_run(tmp_path, window() + simple("c1", 100, 1000), run=run)
    assert refused_code(where) == "e.input.missing.provenance.run.f"


@pytest.mark.parametrize("measure", [["requested"], ["requested", "nonsense"], "requested"])
def test_a_measure_interval_that_is_not_two_milestones_is_refused(tmp_path, measure):
    where = write_run(tmp_path, window() + simple("c1", 100, 1000),
                      run=run_section(measure=measure))
    assert refused_code(where) == "e.input.range.provenance.measure.f"


def test_a_warmup_as_long_as_the_window_is_refused(tmp_path):
    where = write_run(tmp_path, window(10_000) + simple("c1", 100, 1000),
                      run=run_section(warmup_s=0.00001))
    assert refused_code(where) == "e.input.range.provenance.warmup.f"


def test_a_timeline_with_no_request_is_refused(tmp_path):
    where = write_run(tmp_path, window())
    assert refused_code(where) == "e.input.missing.timeline.requested.f"


def test_a_request_on_a_clock_without_an_offset_is_refused(tmp_path):
    where = write_run(tmp_path, window() + [ev("requested", 5, ["c1"], clock="realtime@x")])
    assert refused_code(where) == "e.input.range.timeline.requested.clock.f"


# --- the window, the driver and the intervals --------------------------------------------------

def test_the_header_fields_come_from_the_timeline_and_provenance(tmp_path):
    got = derive(write_run(tmp_path, window() + simple("c1", 100, 1000)))
    assert got["report_schema"] == 1 and got["generator_commit"] == "gc"
    assert got["timeline_schema"] == 1 and got["vocabulary_version"] == 1
    assert got["harness_commit"] == "hc" and got["scenario_sha256"] == "ab" * 32
    assert got["run"] == run_section()
    assert got["census"] == "not measured"
    assert got["reconciliation"] == "not measured"
    assert got["measurement_cost"] == "not measured"
    assert got["refused"] is False and got["refused_reason"] is None
    assert got["warnings"] == []


def test_only_credentials_requested_inside_the_window_after_warmup_count(tmp_path):
    events = (window(10_000) + simple("early", 500, 1000) + simple("in", 2000, 1000)
              + simple("late", 10_000, 100))
    got = derive(write_run(tmp_path, events, run=run_section(warmup_s=0.000001)))
    # warmup 1000 ns: measured from 1000; "early" (500) is warmup, "late" (10000) is past the end.
    assert got["window"] == {"start_ns": 0, "end_ns": 10_000, "warmup_ns": 1000,
                             "measured_from_ns": 1000, "credentials": 1}
    assert got["intervals"]["requested->verified"]["count"] == 1


def test_the_driver_check_reads_requested_and_dispatched(tmp_path):
    events = window(10_000) + simple("c1", 100, 1000, dispatched_lag=7) + [
        ev("requested", 200, ["c2"])]  # never dispatched
    got = derive(write_run(tmp_path, events))
    # c2 never left the driver: the offered load was not what the system saw, so it refuses.
    assert got["driver"]["p99_lag_ns"] == 7 and got["driver"]["backlog"] == [1]
    assert got["driver"]["ok"] is False and "never left" in got["driver"]["reason"]
    assert got["refused"] is True


def test_a_lagging_driver_refuses_the_run_and_says_so(tmp_path):
    events = window(20 * MS) + simple("c1", 100, 1000, dispatched_lag=6 * MS)
    got = derive(write_run(tmp_path, events))
    assert got["refused"] is True
    assert got["driver"]["ok"] is False
    assert "5000000 ns limit" in got["refused_reason"]
    assert any(w.startswith("The run is refused") for w in got["warnings"])


def test_an_interval_entry_quotes_percentiles_and_its_resolution(tmp_path):
    got = derive(write_run(tmp_path, window() + simple("c1", 100, 1000)
                           + simple("c2", 200, 1200) + [ev("requested", 300, ["c3"])]))
    entry = got["intervals"]["requested->verified"]
    # c1 1000 and c2 1200, both multiples of the 500 ns poll -> 1000, 1500 (1200 rounds up).
    assert entry == {"start": "requested", "end": "verified", "pairs": [], "measured": True,
                     "count": 2, "missing": 1,
                     "not_measured": {"count": 0, "reasons": []}, "resolution_ns": 500,
                     "p50": 1000, "p90": 1500, "p99": 1500, "p99.9": 1500, "max": 1500}
    assert got["measure"] == "requested->verified"


def test_a_pair_interval_with_no_samples_says_it_was_not_measured(tmp_path):
    got = derive(write_run(tmp_path, window() + simple("c1", 100, 1000)))
    entry = got["intervals"]["stored->proof_generated"]
    assert entry == {"start": "stored", "end": "proof_generated",
                     "pairs": ["proof_vs_verification"], "measured": False, "count": 0,
                     "missing": 0, "not_measured": {"count": 0, "reasons": []},
                     "resolution_ns": None}


def test_a_cross_domain_interval_without_an_offset_is_not_measured_and_warned(tmp_path):
    events = window() + simple("c1", 100, 1000) + [
        ev("registry_updated", 150, ["c1"]), ev("anchored", 99, ["c1"], clock=SK,
                                                 resolution=1000)]
    got = derive(write_run(tmp_path, events))
    entry = got["intervals"]["registry_updated->anchored"]
    assert entry["measured"] is False and entry["not_measured"]["count"] == 1
    assert "no offset" in entry["not_measured"]["reasons"][0]
    assert any("registry_updated->anchored" in w for w in got["warnings"])
    assert got["observability"]["pairs"]["anchor_vs_witness"] == {
        "measurable": False,
        "gap": "anchored is stamped in stack:imbu, which has no offset to the harness clock "
               "measured in this run."}


def test_an_end_stamped_before_its_start_is_not_measured_rather_than_zero(tmp_path):
    events = window() + simple("c1", 100, 1000) + [
        ev("constructed", 300, ["c1"]), ev("registry_updated", 250, ["c1"])]
    got = derive(write_run(tmp_path, events, run=run_section(
        measure=["constructed", "registry_updated"])))
    entry = got["intervals"]["constructed->registry_updated"]
    assert entry["measured"] is False and entry["not_measured"]["count"] == 1
    assert "inconsistent" in entry["not_measured"]["reasons"][0]


# --- incomplete credentials --------------------------------------------------------------------

def test_censored_credentials_are_lower_bounds_at_the_window_end(tmp_path):
    events = window(10_000) + simple("c1", 100, 1000) + [
        ev("requested", 1000, ["c2"]), ev("requested", 9000, ["c3"])]
    got = derive(write_run(tmp_path, events))
    # Complete: c1 1000. Censored: c2 10000-1000=9000, c3 10000-9000=1000 (ties sort after).
    # Sorted: 1000 (c1), 1000* (c3), 9000* (c2). n=3: p50 rank 2 -> censored, p90.. rank 3.
    # 9000 lies above 2048: bit length 14, bucket width 2**3 = 8, 9000 is its slot's lowest,
    # so the quoted upper bound is 9007.
    assert got["incomplete"] == {
        "count": 2, "credentials": ["c2", "c3"],
        "latency": {"count": 3, "censored": 2,
                    "p50": {"value": 1000, "lower_bound": True},
                    "p90": {"value": 9007, "lower_bound": True},
                    "p99": {"value": 9007, "lower_bound": True},
                    "p99.9": {"value": 9007, "lower_bound": True},
                    "max": {"value": 9000, "lower_bound": True}}}


def test_with_no_placeable_sample_the_censored_latency_is_not_measured(tmp_path):
    events = window() + [ev("requested", 100, ["c1"]), ev("verified", 300, ["c1"], clock=SK,
                                                          method="reported", resolution=1)]
    got = derive(write_run(tmp_path, events))
    assert got["incomplete"] == {"count": 0, "credentials": [],
                                 "latency": {"measured": False}}


def test_the_earliest_event_of_a_name_covers_the_credential(tmp_path):
    events = window() + simple("c1", 100, 1000) + [
        ev("verified", 2000, ["c1"], method="polled", resolution=500)]  # a later repeat
    got = derive(write_run(tmp_path, events))
    assert got["intervals"]["requested->verified"]["max"] == 1000


def test_a_credential_with_the_measure_end_but_not_its_start_is_neither_sample_nor_censored(
        tmp_path):
    events = window() + simple("c1", 100, 1000)
    got = derive(write_run(tmp_path, events, run=run_section(measure=["stored", "verified"])))
    assert got["incomplete"] == {"count": 0, "credentials": [], "latency": {"measured": False}}


def test_a_censored_credential_whose_start_cannot_be_placed_is_counted_not_sampled(tmp_path):
    events = window() + [ev("requested", 100, ["c1"]), ev("stored", 50, ["c1"], clock=SK,
                                                          method="reported", resolution=1)]
    got = derive(write_run(tmp_path, events, run=run_section(measure=["stored", "verified"])))
    assert got["incomplete"]["count"] == 1
    assert got["incomplete"]["latency"] == {"measured": False}


# --- throughput --------------------------------------------------------------------------------

def test_throughput_counts_milestones_inside_the_window_and_skips_unconvertible(tmp_path):
    events = window(10_000) + simple("c1", 100, 1000) + [
        ev("constructed", 500, ["c1", "c2"]),
        ev("constructed", 20_000, ["c3"]),           # after the window
        ev("anchored", 600, ["c1"], clock=SK)]       # no offset: unconvertible
    got = derive(write_run(tmp_path, events))
    t = got["throughput"]
    assert t["window_ns"] == 10_000 and t["offered_per_s"] == 1.0
    # 2 credentials in 10000 ns = 2 * 1e9 / 10000 = 200000 per second.
    assert t["milestones"]["constructed"] == {"count": 2, "per_s": 200_000.0,
                                              "unconvertible": 0}
    assert t["milestones"]["anchored"] == {"count": 0, "per_s": 0.0, "unconvertible": 1}
    assert "registry_updated" not in t["milestones"]
    assert t["pairs"] == {"construction_vs_registry": {"constructed": 200_000.0,
                                                       "registry_updated": None}}


# --- observability -----------------------------------------------------------------------------

def test_the_matrix_names_the_declaring_adapter_and_marks_the_rest_unobserved(tmp_path):
    got = derive(write_run(tmp_path, window() + simple("c1", 100, 1000)))
    matrix = got["observability"]["milestones"]
    assert set(matrix) == set(MILESTONES)
    assert matrix["requested"] == {"adapter": "driver", "role": "harness", "method": "observed",
                                   "clock": "harness", "resolution_ns": 1, "events": 1}
    assert matrix["verified"] == {"adapter": "fake.verifier", "role": "verifier",
                                  "method": "polled", "clock": "harness", "resolution_ns": 500,
                                  "events": 1}
    assert matrix["anchored"]["events"] == 0
    assert matrix["stored"] == "unobserved"
    pairs = got["observability"]["pairs"]
    assert pairs["construction_vs_registry"] == {
        "measurable": False,
        "gap": "constructed is declared, but no constructed event appeared in this run. "
               "registry_updated is declared, but no registry_updated event appeared in this "
               "run."}
    assert pairs["delivery_vs_storage"] == {
        "measurable": False,
        "gap": "No adapter in this run observes grant_sent. No adapter in this run observes "
               "delivered. No adapter in this run observes stored."}
    assert pairs["normal_vs_faulted"] == {
        "measurable": False,
        "gap": "No fault was scheduled in this run, so there is no faulted performance to set "
               "beside normal performance."}


def test_an_adapter_may_declare_another_roles_milestone_and_the_driver_may_declare_its_own(
        tmp_path):
    adapters = {"driver": decl("driver.custom", "issuer", {"requested": ("observed",
                                                                         "harness", 1)}),
                "issuer": decl("fake.issuer", "issuer", {
                    "witnessed": ("reported", "realtime", 1000),
                    "anchored": ("reported", "realtime", 1000),
                    "registry_updated": ("observed", "harness", 1),
                    "constructed": ("observed", "harness", 1)})}
    got = derive(write_run(tmp_path, window() + simple("c1", 100, 1000), adapters=adapters))
    matrix = got["observability"]["milestones"]
    assert matrix["requested"]["adapter"] == "driver.custom"
    assert matrix["witnessed"]["role"] == "issuer"
    # The declared "realtime" clock resolves through provenance's clocks, which name none
    # here, so it stays "realtime" and has no offset.
    assert got["observability"]["pairs"]["anchor_vs_witness"]["gap"] == (
        "anchored is stamped in realtime, which has no offset to the harness clock measured "
        "in this run.")
    assert got["observability"]["pairs"]["construction_vs_registry"]["gap"].startswith(
        "constructed is declared, but no constructed event appeared")


def test_a_throughput_milestone_on_an_unmeasured_clock_is_a_gap(tmp_path):
    adapters = {"issuer": decl("fake.issuer", "issuer", {
        "constructed": ("reported", SK, 1000), "registry_updated": ("observed", "harness", 1)})}
    got = derive(write_run(tmp_path, window() + simple("c1", 100, 1000), adapters=adapters))
    assert got["observability"]["pairs"]["construction_vs_registry"] == {
        "measurable": False,
        "gap": "constructed is stamped in stack:imbu, which has no offset to the harness "
               "clock measured in this run."}


def test_the_faulted_pair_is_measurable_with_a_fault_and_a_measurable_measure(tmp_path):
    events = window() + simple("c1", 100, 1000) + [
        ev("fault_started", 2000, attrs={"kind": "delay", "target": "issuer"}),
        ev("fault_ended", 3000, attrs={"kind": "delay", "target": "issuer"})]
    got = derive(write_run(tmp_path, events))
    assert got["observability"]["pairs"]["normal_vs_faulted"] == {"measurable": True,
                                                                  "gap": None}


# --- faults ------------------------------------------------------------------------------------

def test_a_fault_splits_the_measure_interval_into_before_during_and_after(tmp_path):
    events = (window(10_000) + simple("b", 100, 1000) + simple("d", 2500, 1000)
              + simple("a", 4000, 1000) + [
                  ev("fault_started", 2000, attrs={"kind": "delay", "target": "issuer"}),
                  ev("fault_ended", 3000, attrs={"kind": "delay", "target": "issuer"})])
    [fault] = derive(write_run(tmp_path, events))["faults"]
    assert fault["kind"] == "delay" and fault["target"] == "issuer"
    assert fault["started_ns"] == 2000 and fault["ended_ns"] == 3000
    assert [fault[k]["count"] for k in ("before", "during", "after")] == [1, 1, 1]
    assert "time_to_ready_ns" not in fault


def test_an_unended_fault_runs_to_the_window_end(tmp_path):
    events = window(10_000) + simple("d", 2500, 1000) + [
        ev("fault_started", 2000, attrs={"kind": "partition", "target": "holder"}),
        ev("fault_ended", 1000, attrs={"kind": "partition", "target": "holder"}),  # earlier
        ev("fault_ended", 3000, attrs={"kind": "delay", "target": "holder"})]      # other kind
    [fault] = derive(write_run(tmp_path, events))["faults"]
    assert fault["ended_ns"] is None
    assert fault["during"]["count"] == 1 and fault["after"]["count"] == 0


def test_a_crash_reports_time_to_ready_and_to_first_success(tmp_path):
    events = window(10_000) + simple("c1", 100, 4500) + simple("c0", 50, 4000) + [
        ev("fault_started", 2000, attrs={"kind": "crash", "target": "issuer"}),
        ev("process_killed", 1500, attrs={"process": "issuer"}),   # before the fault: ignored
        ev("process_killed", 2100, attrs={"process": "holder"}),   # another process
        ev("process_killed", 2200, attrs={"process": "issuer"}),
        ev("process_ready", 2150, attrs={"process": "issuer"}),    # before the kill
        ev("process_ready", 3200, attrs={"process": "issuer"}),
        ev("fault_ended", 3200, attrs={"kind": "crash", "target": "issuer"})]
    [fault] = derive(write_run(tmp_path, events))["faults"]
    assert fault["time_to_ready_ns"] == 3200 - 2200
    # verified at 4050 (c0) and 4600 (c1); the first after ready is 4050.
    assert fault["time_to_first_success_ns"] == 4050 - 3200


def test_a_crash_of_a_role_with_several_processes_is_ready_when_the_last_is(tmp_path):
    # this.i 5ed29j5w: "state_distribution" names two processes, and "state_distributor"
    # merely shares a prefix, so it is not one of them.
    crash = {"kind": "crash", "target": "state_distribution"}
    events = window(10_000) + simple("c1", 100, 4500) + [
        ev("fault_started", 2000, attrs=crash),
        ev("process_killed", 2100, attrs={"process": "state_distribution.registrar"}),
        ev("process_killed", 2150, attrs={"process": "state_distribution.observer"}),
        ev("process_killed", 2120, attrs={"process": "state_distributor"}),
        ev("process_ready", 2600, attrs={"process": "state_distribution.registrar"}),
        ev("process_ready", 3900, attrs={"process": "state_distribution.observer"}),
        ev("process_ready", 4000, attrs={"process": "state_distributor"}),
        ev("fault_ended", 3900, attrs=crash)]
    [fault] = derive(write_run(tmp_path, events))["faults"]
    assert fault["time_to_ready_ns"] == 3900 - 2100
    assert fault["time_to_first_success_ns"] == 4600 - 3900
    # One of the two never came back: the role was never ready.
    events = [e for e in events if not (e.name == "process_ready" and e.t == 3900)]
    [fault] = derive(write_run(tmp_path / "b", events))["faults"]
    assert fault["time_to_ready_ns"] is None


def test_a_crash_with_no_restart_or_no_success_reports_none(tmp_path):
    crash = [ev("fault_started", 2000, attrs={"kind": "crash", "target": "issuer"})]
    never_ready = window() + simple("c1", 100, 1000) + crash + [
        ev("process_killed", 2100, attrs={"process": "issuer"})]
    [fault] = derive(write_run(tmp_path / "a", never_ready))["faults"]
    assert fault["time_to_ready_ns"] is None and fault["time_to_first_success_ns"] is None
    no_success = window() + simple("c1", 100, 1000) + crash + [
        ev("process_killed", 2100, attrs={"process": "issuer"}),
        ev("process_ready", 2300, attrs={"process": "issuer"})]
    [fault] = derive(write_run(tmp_path / "b", no_success))["faults"]
    assert fault["time_to_ready_ns"] == 200 and fault["time_to_first_success_ns"] is None
    never_killed = window() + simple("c1", 100, 1000) + crash
    [fault] = derive(write_run(tmp_path / "c", never_killed))["faults"]
    assert fault["time_to_ready_ns"] is None


# --- provenance-fed sections and warnings ------------------------------------------------------

def test_reconciliation_and_measurement_cost_come_from_provenance(tmp_path):
    cost = {"links": {"issuer": {"p50_ns": 200_000, "p99_ns": 800_000}}, "harness_cpu_s": 1.5}
    events = window() + simple("c1", 100, 1000)
    got = derive(write_run(tmp_path, events, measurement_cost=cost, reconciliation={
        "verifier": {"c1": {"status": "valid", "said": "E1"}}, "anchors": {"c1": 2}}))
    assert got["measurement_cost"] == cost
    assert got["reconciliation"] == {
        "summary": {"total": 1, "done": 1, "resumed": 0, "lost": 0, "double_anchored": 1,
                    "two_saids": 0},
        "credentials": {"c1": {"fate": "done", "double_anchored": True, "two_saids": False,
                               "saids": ["E1"]}}}


def test_an_untested_component_version_is_warned(tmp_path):
    got = derive(write_run(tmp_path, window() + simple("c1", 100, 1000),
                           components={"issuer": {"stack": "2"}}))
    assert got["warnings"] == [
        "The issuer adapter fake.issuer ran against stack 2, which it was never "
        "contract-tested against (it was tested against 1)."]
    got = derive(write_run(tmp_path, window() + simple("c1", 100, 1000),
                           components={"issuer": {"other": "9"}}))
    assert got["warnings"] == [
        "The issuer adapter fake.issuer ran against other 9, which it was never "
        "contract-tested against (it was tested against no version of it)."]


# --- writing and rendering ---------------------------------------------------------------------

def test_write_report_writes_json_and_markdown_beside_the_inputs(tmp_path):
    where = write_run(tmp_path, window() + simple("c1", 100, 1000))
    written = report.write_report(where, generator_commit="gc")
    assert json.loads((where / "report.json").read_text()) == written
    assert (where / "report.md").read_text() == report.render_markdown(written)


def test_the_markdown_quotes_every_pair_and_carries_the_key_numbers(tmp_path):
    cost = {"links": {"issuer": {"p50_ns": 200_000, "p99_ns": 800_000}}, "harness_cpu_s": 1.5}
    events = (window(10_000) + simple("c1", 100, 1000) + simple("c2", 200, 1200)
              + [ev("requested", 9000, ["c3"]), ev("dispatched", 9001, ["c3"]),
                 ev("constructed", 500, ["c1"]),
                 ev("registry_updated", 600, ["c1"]), ev("anchored", 1, ["c1"], clock=SK),
                 ev("fault_started", 2000, attrs={"kind": "crash", "target": "issuer"}),
                 ev("process_killed", 2100, attrs={"process": "issuer"}),
                 ev("process_ready", 2300, attrs={"process": "issuer"})])
    got = derive(write_run(tmp_path, events, components={"issuer": {"stack": "2"}},
                           measurement_cost=cost, reconciliation={
                               "verifier": {"c1": {"status": "valid", "said": None}},
                               "anchors": {}}))
    text = report.render_markdown(got)
    for pair in PAIRS:
        assert f"“{pair.utah}”" in text
    for needle in ("requested->verified", "p99.9", "1500 ns", "fake.verifier", "unobserved",
                   "200000.0", "crash on issuer", "time to ready: 200 ns",
                   "time to first success: not seen", "ended: not before the window closed",
                   "lost: 2", "issuer: p50 200000 ns, p99 800000 ns", "harness CPU: 1.5 s",
                   "never contract-tested", "not measured", "lower bound",
                   "No adapter in this run observes", "Driver check passed",
                   "The signer census is not measured"):
        assert needle in text, needle


def test_the_markdown_says_what_was_not_measured_and_when_the_run_was_refused(tmp_path):
    events = window(20 * MS) + simple("c1", 100, 1000, dispatched_lag=6 * MS)
    text = report.render_markdown(derive(write_run(tmp_path, events)))
    assert "This run is refused" in text
    assert "Reconciliation was not measured" in text
    assert "Measurement cost was not measured" in text
    assert "No faults were scheduled" in text
    assert "Warnings" in text
    clean = report.render_markdown(derive(write_run(tmp_path / "ok", window()
                                                    + [ev("requested", 100, ["c1"]),
                                                       ev("dispatched", 101, ["c1"])])))
    assert "No warnings" in clean
    unplaced = window() + [ev("requested", 100, ["c1"]),
                           ev("verified", 300, ["c1"], clock=SK, method="reported")]
    text = report.render_markdown(derive(write_run(tmp_path / "unplaced", unplaced)))
    assert "no sample could be placed" in text


def test_a_non_crash_fault_renders_without_recovery_times(tmp_path):
    events = window() + simple("c1", 100, 1000) + [
        ev("fault_started", 2000, attrs={"kind": "duplicate", "target": "holder"}),
        ev("fault_ended", 3000, attrs={"kind": "duplicate", "target": "holder"})]
    text = report.render_markdown(derive(write_run(tmp_path, events)))
    assert "### duplicate on holder" in text and "ended: 3000 ns" in text
    assert "time to ready" not in text


# --- golden timelines (Part B #2) --------------------------------------------------------------
#
# Each directory under tests/golden/v<timeline_schema>/<name>/ holds a hand-written timeline.jsonl,
# its provenance.json, and the frozen expected-report.json. Two checks run per directory:
#   1. derive() reproduces expected-report.json exactly (except generator_commit), so every past
#      timeline schema keeps producing its recorded report. Adding tests/golden/v2/ later needs no
#      code here.
#   2. The frozen file agrees with the values computed BY HAND below, so the frozen report was not
#      merely whatever the code produced the day it was written.
#
# Arithmetic notes shared by both goldens:
#   - An interval within one clock has resolution max(start.res, end.res, 1); across clocks it adds
#     both offset bounds (the harness clock's bound is 0). reported() = res * ceil(value / res).
#   - Percentile = nearest rank, rank = ceil(p/100 * n), quoted as the HDR slot's upper bound.
#     Below 2048 the slot is the value itself. A value v in [2**(b-1), 2**b) with b > 11 sits in a
#     slot of width 2**(b-11) starting at v rounded down to that width.
#   - Throughput per second = count * 1e9 / (run_ended - (run_started + warmup)).

GOLDEN = Path(__file__).parent / "golden"
GOLDEN_DIRS = sorted(p.parent for p in GOLDEN.glob("v*/*/timeline.jsonl"))


def unmeasured(*names):
    return {"measured": False, "count": 0, "missing": 0, "resolution_ns": None,
            "not_measured": {"count": 0, "reasons": []}}


def stats(count, missing, resolution, p50, p90, p99, p999, top):
    return {"measured": True, "count": count, "missing": missing, "resolution_ns": resolution,
            "not_measured": {"count": 0, "reasons": []},
            "p50": p50, "p90": p90, "p99": p99, "p99.9": p999, "max": top}


def entry(start, end, pairs, body):
    return {"start": start, "end": end, "pairs": pairs, **body}


EMPTY_PAIR_INTERVALS = {
    "stored->proof_generated": entry("stored", "proof_generated", ["proof_vs_verification"],
                                     unmeasured()),
    "proof_generated->verified": entry("proof_generated", "verified",
                                       ["proof_vs_verification"], unmeasured()),
    "grant_sent->delivered": entry("grant_sent", "delivered", ["delivery_vs_storage"],
                                   unmeasured()),
    "delivered->stored": entry("delivered", "stored", ["delivery_vs_storage"], unmeasured()),
    "sync_full_started->sync_full_done": entry("sync_full_started", "sync_full_done",
                                               ["sync_full_vs_incremental"], unmeasured()),
    "published->sync_increment_applied": entry("published", "sync_increment_applied",
                                               ["sync_full_vs_incremental"], unmeasured()),
    "revocation_requested->revocation_published": entry(
        "revocation_requested", "revocation_published", ["revocation_vs_recognition"],
        unmeasured()),
    "revocation_published->revocation_recognised": entry(
        "revocation_published", "revocation_recognised", ["revocation_vs_recognition"],
        unmeasured()),
}

UNOBSERVED_GAPS = {
    "proof_vs_verification": "No adapter in this run observes stored. No adapter in this run "
                             "observes proof_generated.",
    "delivery_vs_storage": "No adapter in this run observes grant_sent. No adapter in this run "
                           "observes delivered. No adapter in this run observes stored.",
    "sync_full_vs_incremental": "No adapter in this run observes sync_full_started. No adapter "
                                "in this run observes sync_full_done. No adapter in this run "
                                "observes published. No adapter in this run observes "
                                "sync_increment_applied.",
    "revocation_vs_recognition": "No adapter in this run observes revocation_requested. No "
                                 "adapter in this run observes revocation_published. No "
                                 "adapter in this run observes revocation_recognised.",
}
PAIR_VERDICTS = {"construction_vs_registry": {"measurable": True, "gap": None},
                 "anchor_vs_witness": {"measurable": True, "gap": None},
                 "normal_vs_faulted": {"measurable": True, "gap": None},
                 **{k: {"measurable": False, "gap": v} for k, v in UNOBSERVED_GAPS.items()}}

# ---- singles: c1, c2, c3, each its own events --------------------------------------------------
# Window: run_started 0, run_ended 11000, warmup 0.000001 s = 1000 ns -> measured from 1000, span
# 10000. Requests at 1000 (boundary, included), 2000, 3000: three credentials.
# stack:imbu offset samples (-1000000, bracket 10) and (-999996, bracket 6):
#   offset = (-1000000 + -999996) // 2 = -999998; bound = max(10, 6) + (1000000 - 999996) = 14.
# Harness times of the stack events: anchored 1001448 -> 1450, 1002598 -> 2600, 1003548 -> 3550;
#   witnessed 1001698 -> 1700, 1002718 -> 2720.
S_RES_X = 100 + 14  # anchored/witnessed res 100, plus the offset bound, against a 1 ns harness end
SINGLES = {
    "window": {"start_ns": 0, "end_ns": 11000, "warmup_ns": 1000, "measured_from_ns": 1000,
               "credentials": 3},
    # lags 1003-1000=3, 2002-2000=2, 3001-3000=1; p99 rank ceil(2.97)=3 -> 3. One 1 s window,
    # all three dispatched by its end -> backlog [0]. 5.0 ms = 5000000 ns.
    "driver": {"ok": True, "p99_lag_ns": 3, "backlog": [0], "reason": None,
               "lag_limit_ns": 5_000_000},
    "refused": False,
    "measure": "requested->verified",
    "intervals": {
        # 150 (c1), 200 (c2), 50 (c3), res 114: ceil -> 2*114=228, 228, 1*114=114.
        # sorted [114, 228, 228]: p50 rank 2 = 228, p90.. rank 3 = 228.
        "registry_updated->anchored": entry("registry_updated", "anchored",
                                            ["anchor_vs_witness"],
                                            stats(3, 0, S_RES_X, 228, 228, 228, 228, 228)),
        # both stack:imbu, res 100: 1001698-1001448=250 -> 300; 1002718-1002598=120 -> 200.
        # c3 has no witnessed: missing 1. sorted [200, 300]: p50 rank 1 = 200, p90 rank 2 = 300.
        "anchored->witnessed": entry("anchored", "witnessed", ["anchor_vs_witness"],
                                     stats(2, 1, 100, 200, 300, 300, 300, 300)),
        **EMPTY_PAIR_INTERVALS,
        # polled res 500: 2000-1000=1000 -> 1000; 3500-2000=1500 -> 1500; c3 missing.
        "requested->verified": entry("requested", "verified", [],
                                     stats(2, 1, 500, 1000, 1500, 1500, 1500, 1500)),
        # 100, 150, 50 -> sorted [50, 100, 150]
        "requested->accepted": entry("requested", "accepted", [],
                                     stats(3, 0, 1, 100, 150, 150, 150, 150)),
        # Lifecycle steps. 1200-1100=100, 2300-2150=150, 3400-3050=350 -> sorted [100, 150, 350]
        "accepted->constructed": entry("accepted", "constructed", [],
                                       stats(3, 0, 1, 150, 350, 350, 350, 350)),
        # 100 each
        "constructed->registry_updated": entry("constructed", "registry_updated", [],
                                               stats(3, 0, 1, 100, 100, 100, 100, 100)),
        # published is not observed, so witnessed is followed by verified. Res max(100, 500) + 14
        # = 514. c1: 2000-1700=300 -> 514; c2: 3500-2720=780 -> 2*514=1028. c3 never witnessed,
        # so it lacks the start and is not missing. sorted [514, 1028].
        "witnessed->verified": entry("witnessed", "verified", [],
                                     stats(2, 0, 514, 514, 1028, 1028, 1028, 1028)),
        # 200, 300, 400
        "requested->constructed": entry("requested", "constructed", [],
                                        stats(3, 0, 1, 300, 400, 400, 400, 400)),
        # 300, 400, 500
        "requested->registry_updated": entry("requested", "registry_updated", [],
                                             stats(3, 0, 1, 400, 500, 500, 500, 500)),
        # 450, 600, 550 at res 114: ceil(3.95)=4 -> 456, ceil(5.26)=6 -> 684, ceil(4.82)=5 -> 570
        "requested->anchored": entry("requested", "anchored", [],
                                     stats(3, 0, S_RES_X, 570, 684, 684, 684, 684)),
        # 700, 720 at res 114: both ceil(6.1..6.3)=7 -> 798; c3 missing
        "requested->witnessed": entry("requested", "witnessed", [],
                                      stats(2, 1, S_RES_X, 798, 798, 798, 798, 798)),
    },
    # c3 never verified: censored at 11000 - 3000 = 8000. Samples (1000), (1500), (8000*).
    # 8000 is in [4096, 8192): slot width 2**(13-11) = 4, 8000 is a multiple, upper bound 8003.
    # p50 rank 2 -> 1500 (complete); p90/p99/p99.9 rank 3 -> 8003 (censored); max 8000 (censored).
    "incomplete": {"count": 1, "credentials": ["c3"],
                   "latency": {"count": 3, "censored": 1,
                               "p50": {"value": 1500, "lower_bound": False},
                               "p90": {"value": 8003, "lower_bound": True},
                               "p99": {"value": 8003, "lower_bound": True},
                               "p99.9": {"value": 8003, "lower_bound": True},
                               "max": {"value": 8000, "lower_bound": True}}},
    # 3 credentials in 10000 ns = 3 * 1e9 / 10000 = 300000/s; 2 -> 200000/s.
    "throughput": {"window_ns": 10000, "offered_per_s": 1000.0,
                   "milestones": {
                       "requested": {"count": 3, "per_s": 300000.0, "unconvertible": 0},
                       "accepted": {"count": 3, "per_s": 300000.0, "unconvertible": 0},
                       "constructed": {"count": 3, "per_s": 300000.0, "unconvertible": 0},
                       "registry_updated": {"count": 3, "per_s": 300000.0, "unconvertible": 0},
                       "anchored": {"count": 3, "per_s": 300000.0, "unconvertible": 0},
                       "witnessed": {"count": 2, "per_s": 200000.0, "unconvertible": 0},
                       "verified": {"count": 2, "per_s": 200000.0, "unconvertible": 0}},
                   "pairs": {"construction_vs_registry": {"constructed": 300000.0,
                                                          "registry_updated": 300000.0}}},
    # delay on witness_pool, 2500..6000. Before: c1 (1000), c2 (2000) -> 1000, 1500 as above.
    # During: c3 (3000), never verified -> missing 1. After: nobody.
    "faults": [{"kind": "delay", "target": "witness_pool", "started_ns": 2500,
                "ended_ns": 6000,
                "before": stats(2, 0, 500, 1000, 1500, 1500, 1500, 1500),
                "during": {**unmeasured(), "missing": 1},
                "after": unmeasured()}],
    # Lifecycles on the harness clock: c1 1000..2000 misses 2500..6000 -> done; c2 2000..3500
    # overlaps -> resumed; c3 unknown -> lost. One SAID each, one anchor each.
    "reconciliation": {
        "summary": {"total": 3, "done": 1, "resumed": 1, "lost": 1, "double_anchored": 0,
                    "two_saids": 0},
        "credentials": {
            "c1": {"fate": "done", "double_anchored": False, "two_saids": False,
                   "saids": ["Ec1"]},
            "c2": {"fate": "resumed", "double_anchored": False, "two_saids": False,
                   "saids": ["Ec2"]},
            "c3": {"fate": "lost", "double_anchored": False, "two_saids": False,
                   "saids": ["Ec3"]}}},
    "measurement_cost": {"links": {"issuer": {"p50_ns": 210000, "p99_ns": 640000}},
                         "harness_cpu_s": 0.25},
    "failures": {"count": 0, "by_stage": {}, "by_code": {}, "fault_failed": None},
    "warnings": ["The verifier adapter golden.verifier ran against heti 0.9.1, which it was "
                 "never contract-tested against (it was tested against 0.9)."],
    "pairs": PAIR_VERDICTS,
    "events": {"requested": 3, "accepted": 3, "constructed": 3, "registry_updated": 3,
               "anchored": 3, "witnessed": 2, "verified": 2},
}

# ---- batch: b1, b2, b3 share one anchored and one witnessed event (batch-1) -------------------
# Window as above: measured from 1000, span 10000. Requests 1000, 1100, 1200.
# realtime@golden-host samples (-2000, bracket 20) twice: offset -2000, bound 20 + 0 = 20.
# The shared anchored 3800 -> 1800 and witnessed 4600 -> 2600 on the harness clock.
B_RES_X = 1000 + 20  # reported res 1000 plus the bound, against a 1 ns harness end
BATCH = {
    "window": {"start_ns": 0, "end_ns": 11000, "warmup_ns": 1000, "measured_from_ns": 1000,
               "credentials": 3},
    "driver": {"ok": True, "p99_lag_ns": 1, "backlog": [0], "reason": None,
               "lag_limit_ns": 5_000_000},
    "refused": False,
    "measure": "requested->verified",
    "intervals": {
        # 1800-1350=450, 1800-1450=350, 1800-1550=250: each ceil(<1) -> 1020.
        "registry_updated->anchored": entry("registry_updated", "anchored",
                                            ["anchor_vs_witness"],
                                            stats(3, 0, B_RES_X, 1020, 1020, 1020, 1020, 1020)),
        # one shared pair of events, same clock, res 1000: 4600-3800=800 -> 1000, three times.
        "anchored->witnessed": entry("anchored", "witnessed", ["anchor_vs_witness"],
                                     stats(3, 0, 1000, 1000, 1000, 1000, 1000, 1000)),
        **EMPTY_PAIR_INTERVALS,
        # polled res 250: 2750-1000=1750 -> 1750; 4000-1100=2900 -> 12*250=3000;
        # 4300-1200=3100 -> 13*250=3250. 3000 and 3250 are in [2048, 4096): slot width 2, both
        # even, so their upper bounds are 3001 and 3251. sorted [1750, 3000, 3250]:
        # p50 rank 2 -> 3001; p90.. rank 3 -> 3251; max is exact, 3250.
        "requested->verified": entry("requested", "verified", [],
                                     stats(3, 0, 250, 3001, 3251, 3251, 3251, 3250)),
        # 1300-1000, 1400-1100, 1500-1200 = 300 each
        "requested->constructed": entry("requested", "constructed", [],
                                        stats(3, 0, 1, 300, 300, 300, 300, 300)),
        # Lifecycle steps. 1350-1300, 1450-1400, 1550-1500 = 50 each
        "constructed->registry_updated": entry("constructed", "registry_updated", [],
                                               stats(3, 0, 1, 50, 50, 50, 50, 50)),
        # witnessed 2600 (harness) to verified 2750, 4000, 4300 = 150, 1400, 1700, res
        # max(1000, 250) + 20 = 1020 -> 1020, 2040, 2040 (below 2048: exact).
        "witnessed->verified": entry("witnessed", "verified", [],
                                     stats(3, 0, 1020, 2040, 2040, 2040, 2040, 2040)),
        # 350 each
        "requested->registry_updated": entry("requested", "registry_updated", [],
                                             stats(3, 0, 1, 350, 350, 350, 350, 350)),
        # 800, 700, 600 at res 1020 -> 1020 each
        "requested->anchored": entry("requested", "anchored", [],
                                     stats(3, 0, B_RES_X, 1020, 1020, 1020, 1020, 1020)),
        # 1600, 1500, 1400 at res 1020 -> 2 * 1020 = 2040 each (below 2048: exact)
        "requested->witnessed": entry("requested", "witnessed", [],
                                      stats(3, 0, B_RES_X, 2040, 2040, 2040, 2040, 2040)),
    },
    # Nothing censored; the same three samples as requested->verified, none a lower bound.
    "incomplete": {"count": 0, "credentials": [],
                   "latency": {"count": 3, "censored": 0,
                               "p50": {"value": 3001, "lower_bound": False},
                               "p90": {"value": 3251, "lower_bound": False},
                               "p99": {"value": 3251, "lower_bound": False},
                               "p99.9": {"value": 3251, "lower_bound": False},
                               "max": {"value": 3250, "lower_bound": False}}},
    # One anchored line covers three credentials, so anchored counts 3, not 1.
    "throughput": {"window_ns": 10000, "offered_per_s": 1000.0,
                   "milestones": {
                       name: {"count": 3, "per_s": 300000.0, "unconvertible": 0}
                       for name in ("requested", "constructed", "registry_updated",
                                    "anchored", "witnessed", "verified")},
                   "pairs": {"construction_vs_registry": {"constructed": 300000.0,
                                                          "registry_updated": 300000.0}}},
    # crash on issuer 3000..3510; all requests (<= 1200) are before it.
    # killed 3010, ready 3510 -> 500. First verified at or after 3510 is 4000 -> 490.
    "faults": [{"kind": "crash", "target": "issuer", "started_ns": 3000, "ended_ns": 3510,
                "before": stats(3, 0, 250, 3001, 3251, 3251, 3251, 3250),
                "during": unmeasured(), "after": unmeasured(),
                "time_to_ready_ns": 500, "time_to_first_success_ns": 490}],
    # Harness-clock lifecycles: b1 1000..2750 misses 3000..3510 -> done; b2 1100..4000 and
    # b3 1200..4300 overlap -> resumed (b3 revoked still counts as done-then-resumed).
    # b2 anchored twice; b3 has Eb3 on its event and Eb3-dup from the verifier.
    "reconciliation": {
        "summary": {"total": 3, "done": 1, "resumed": 2, "lost": 0, "double_anchored": 1,
                    "two_saids": 1},
        "credentials": {
            "b1": {"fate": "done", "double_anchored": False, "two_saids": False,
                   "saids": ["Eb1"]},
            "b2": {"fate": "resumed", "double_anchored": True, "two_saids": False,
                   "saids": ["Eb2"]},
            "b3": {"fate": "resumed", "double_anchored": False, "two_saids": True,
                   "saids": ["Eb3", "Eb3-dup"]}}},
    "measurement_cost": "not measured",
    "failures": {"count": 0, "by_stage": {}, "by_code": {}, "fault_failed": None},
    "warnings": [],
    "pairs": PAIR_VERDICTS,
    "events": {"requested": 3, "constructed": 3, "registry_updated": 3, "anchored": 1,
               "witnessed": 1, "verified": 3},
}

HAND = {"singles": SINGLES, "batch": BATCH}


def frozen(where: Path) -> dict:
    return json.loads((where / "expected-report.json").read_text())


@pytest.mark.parametrize("where", GOLDEN_DIRS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_golden_timeline_still_produces_its_recorded_report(where):
    expected = frozen(where)
    got = json.loads(json.dumps(derive(where)))
    expected.pop("generator_commit")
    got.pop("generator_commit")
    assert got == expected


@pytest.mark.parametrize("where", GOLDEN_DIRS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_golden_report_agrees_with_its_hand_computed_values(where):
    hand = HAND[where.name]
    stored = frozen(where)
    for section in ("window", "driver", "refused", "measure", "intervals", "incomplete",
                    "throughput", "faults", "reconciliation", "measurement_cost", "warnings",
                    "failures"):
        assert stored[section] == hand[section], section
    assert stored["census"] == "not measured"
    assert stored["observability"]["pairs"] == hand["pairs"]
    events = {name: seen["events"] for name, seen in stored["observability"]["milestones"].items()
              if seen != "unobserved"}
    assert events == hand["events"]


def test_the_batch_golden_has_one_anchor_line_for_three_credentials():
    lines = (GOLDEN / "v1" / "batch" / "timeline.jsonl").read_text().splitlines()[1:]
    anchors = [json.loads(line) for line in lines if '"anchored"' in line]
    assert len(anchors) == 1
    assert anchors[0]["credentials"] == ["b1", "b2", "b3"] and anchors[0]["batch_id"]


def test_the_golden_markdown_carries_the_hand_computed_numbers():
    singles = report.render_markdown(frozen(GOLDEN / "v1" / "singles"))
    assert "- p90: 8003 ns (lower bound)" in singles and "- p50: 1500 ns\n" in singles
    assert "| registry_updated->anchored | 3 | 0 | 0 | 114 ns | 228 ns |" in singles
    batch = report.render_markdown(frozen(GOLDEN / "v1" / "batch"))
    assert "- time to ready: 500 ns" in batch and "- time to first success: 490 ns" in batch
    assert "- double_anchored: 1" in batch and "- resumed: 2" in batch


def _with_calibration(tmp_path, calibration):
    import shutil
    source = GOLDEN / "v1" / "singles"
    for name in ("timeline.jsonl", "provenance.json"):
        shutil.copy(source / name, tmp_path / name)
    record = json.loads((tmp_path / "provenance.json").read_text())
    record["calibration"] = calibration
    (tmp_path / "provenance.json").write_text(json.dumps(record))
    return report.derive(tmp_path / "timeline.jsonl", tmp_path / "provenance.json",
                         generator_commit="x")


def test_a_run_without_calibration_says_its_numbers_have_no_error_budget(tmp_path):
    got = _with_calibration(tmp_path, "not calibrated")
    assert got["calibration"] == "not calibrated"
    assert "This run carries no calibration." in report.render_markdown(got)
    assert any("no calibration" in w for w in got["warnings"])


def test_a_failed_or_foreign_calibration_is_named(tmp_path):
    budget = {"ok": False, "harness_commit": "other",
              "budget": {"a->b": {"p50_error_ns": 120, "p99_error_ns": -40}}}
    got = _with_calibration(tmp_path, budget)
    text = report.render_markdown(got)
    assert "FAILED" in text and "- a->b: p50 120 ns, p99 -40 ns" in text
    assert got["calibration"] == budget
    assert any("did not pass" in w for w in got["warnings"])
    assert any("made by harness other" in w for w in got["warnings"])
    good = {"ok": True, "harness_commit": "golden-harness", "budget": {}}
    got = _with_calibration(tmp_path, good)
    assert not [w for w in got["warnings"] if "calibration" in w]
    assert "passed" in report.render_markdown(got)


def test_failed_stages_are_counted_and_a_failed_fault_refuses_the_run(tmp_path):
    events = window() + simple("c1", 100, 1000) + [
        ev("stage_failed", 1100, ["c1"], attrs={"stage": "verify", "code": "e.proof.x.f"}),
        ev("fault_failed", 1200, attrs={"kind": "delay", "target": "holder.api",
                                        "error": "no such link"}),
        ev("fault_failed", 1300, attrs={"kind": "crash", "target": "issuer", "error": "x"})]
    got = derive(write_run(tmp_path, events))
    assert got["failures"] == {"count": 1, "by_stage": {"verify": 1},
                               "by_code": {"e.proof.x.f": 1},
                               "fault_failed": "The scheduled delay fault on holder.api could "
                                               "not be applied: no such link"}
    assert got["refused"] is True and got["refused_reason"].startswith("The scheduled delay")
    assert any(w.startswith("1 lifecycle stage(s) failed") for w in got["warnings"])
    assert any(w.startswith("The run is refused. The scheduled") for w in got["warnings"])


def test_a_pair_whose_ends_never_meet_in_one_credential_is_a_gap(tmp_path):
    events = window() + simple("c1", 100, 1000) + simple("c2", 200, 1200) + [
        ev("constructed", 300, ["c1"]), ev("registry_updated", 400, ["c2"]),
        ev("anchored", 500, ["c1"]), ev("witnessed", 600, ["c2"])]
    adapters_ = {"issuer": decl("fake.issuer", "issuer", {
        "constructed": ("observed", "harness", 1), "registry_updated": ("observed", "harness", 1),
        "anchored": ("observed", "harness", 1), "witnessed": ("observed", "harness", 1)})}
    got = derive(write_run(tmp_path, events, adapters=adapters_))
    assert got["observability"]["pairs"]["anchor_vs_witness"]["gap"] == (
        "No credential in the measured window has both registry_updated and anchored. No "
        "credential in the measured window has both anchored and witnessed.")


def test_failure_attributes_of_any_shape_are_counted_as_text(tmp_path):
    events = window() + simple("c1", 100, 1000) + [
        ev("stage_failed", 1100, ["c1"], attrs={"stage": ["verify"], "code": 7})]
    got = derive(write_run(tmp_path, events))
    assert got["failures"]["by_stage"] == {"['verify']": 1}
    assert got["failures"]["by_code"] == {"7": 1}


def test_a_pair_seen_only_during_warmup_is_a_gap(tmp_path):
    adapters_ = {"issuer": decl("fake.issuer", "issuer", {
        "registry_updated": ("observed", "harness", 1), "anchored": ("observed", "harness", 1),
        "witnessed": ("observed", "harness", 1)})}
    events = window(1_000_000) + [ev("requested", 0, ["w"]), ev("dispatched", 1, ["w"]),
                                  ev("registry_updated", 5, ["w"]), ev("anchored", 6, ["w"]),
                                  ev("witnessed", 7, ["w"])] + simple("c1", 100_000, 1000)
    got = derive(write_run(tmp_path, events, adapters=adapters_,
                           run=run_section(warmup_s=0.00005)))
    assert got["window"]["credentials"] == 1
    assert got["observability"]["pairs"]["anchor_vs_witness"]["measurable"] is False


# --- soak drift (this.i 7f9jfc99) -----------------------------------------------------------

S = 1_000_000_000


def hdr(v):
    """The quoted upper bound of v's slot, by hand: 2048 exact sub-buckets, then width 2**b
    for b = bit_length(v) - 11 (the layout tests/test_stats.py checks)."""
    b = max(0, v.bit_length() - 11)
    return (v >> b << b) + (1 << b) - 1


def soak_run(tmp_path, latencies, *, preseed=None, footprints=(), bucket=2, per=10,
             sample_s=None, declared=None):
    """Credentials requested one per second, each verified after the given latency (ms)."""
    events = [ev("run_started", 0), ev("run_ended", len(latencies) * S)]
    if preseed is not None:
        events.append(ev("preseed_ended", -S, attrs={"requested": preseed[0],
                                                       "failed": preseed[1]}))
    for t, size in footprints:
        events.append(ev("footprint", t, attrs={"bytes": size}))
    for i, ms in enumerate(latencies):
        if ms is None:
            events.append(ev("requested", i * S, [f"c{i}"]))
            continue
        events += simple(f"c{i}", i * S, ms * MS)
    soak = {"bucket": bucket, "per_credentials": per}
    if sample_s is not None:
        soak["sample_s"] = sample_s
    if declared is None and preseed is not None:
        declared = preseed[0]
    extra = {"preseed": {"credentials": declared, "concurrency": 2}} if declared else {}
    return derive(write_run(tmp_path, events, run=run_section(soak=soak, **extra)))


def test_a_run_without_a_soak_has_no_drift_section(tmp_path):
    out = derive(write_run(tmp_path, window() + simple("c1", 0, 1000)))
    assert "drift" not in out


def test_drift_buckets_credentials_in_request_order_and_fits_each_position(tmp_path):
    # Buckets of two: latencies (10, 30), (50, 70), (90, 110) ms. Each bucket's p50 is its lower
    # value, its p99 its upper (nearest rank). Store positions: preseed 100 - 4 failed = 96,
    # plus the bucket's midpoint credential index 1, 3, 5 -> 97, 99, 101. Elapsed: requests at
    # 1, 3, 5 s. p50s 10, 50, 90 ms rise 40 ms per 2 credentials = 20 ms per credential, 200 ms
    # per 10 credentials; per hour, 20 ms/s * 3600. Bytes: samples 1000 at 0 s and 3000 at 3 s;
    # the bucket at 1 s reads 1000, at 3 s and 5 s reads 3000.
    out = soak_run(tmp_path, [10, 30, 50, 70, 90, 110], preseed=(100, 4),
                   footprints=[(0, 1000), (3 * S, 3000)])["drift"]
    assert (out["bucket"], out["per_credentials"], out["preseeded"]) == (2, 10, 96)
    assert [(b["store_credentials"], b["elapsed_s"], b["store_bytes"], b["credentials"])
            for b in out["buckets"]] == [(97, 1.0, 1000, 2), (99, 3.0, 3000, 2),
                                         (101, 5.0, 3000, 2)]
    assert [b["p50_ns"] for b in out["buckets"]] == [hdr(10 * MS), hdr(50 * MS), hdr(90 * MS)]
    assert [b["p99_ns"] for b in out["buckets"]] == [hdr(30 * MS), hdr(70 * MS), hdr(110 * MS)]
    # The fit runs on the quoted upper bounds, which sit within 0.1% of the hand values.
    p50 = out["slopes"]["p50"]
    assert p50["store_credentials"]["slope_ns"] == pytest.approx(200 * MS, rel=1e-3)
    assert p50["store_credentials"]["low_ns"] < p50["store_credentials"]["slope_ns"] \
        < p50["store_credentials"]["high_ns"]
    assert p50["store_credentials"]["n"] == 3
    assert p50["elapsed"]["slope_ns"] == pytest.approx(20 * MS * 3600, rel=1e-3)
    assert out["slopes"]["p99"]["store_credentials"]["slope_ns"] == \
        pytest.approx(200 * MS, rel=1e-3)
    # Bytes fit: x = 1000, 3000, 3000 against 10, 50, 90 ms; xbar 7000/3, a hand OLS slope of
    # (sum dx*dy)/(sum dx^2) = (-4000/3*-40 + 2000/3*0 + 2000/3*40)/(16e6/9+4e6/9+4e6/9) ms per
    # byte = 80000/(24e6/9) = 0.03 ms per byte, so 0.03 * 2**20 ms per MiB.
    assert p50["store_bytes"]["slope_ns"] == pytest.approx(0.03 * MS * 2**20, rel=1e-3)
    assert out["units"] == {"store_credentials": "per 10 credentials", "elapsed": "per hour",
                            "store_bytes": "per MiB"}


def test_a_bucket_counts_its_censored_credentials_and_a_partial_bucket_is_dropped(tmp_path):
    out = soak_run(tmp_path, [10, None, 30, 40, 50, 60, 70])["drift"]
    assert [(b["credentials"], b["missing"]) for b in out["buckets"]] == [(1, 1), (2, 0),
                                                                          (2, 0)]
    assert out["preseeded"] == 0
    assert out["buckets"][0]["store_bytes"] is None


def test_too_few_buckets_or_no_size_samples_leave_a_slope_not_measured(tmp_path):
    out = soak_run(tmp_path, [10, 20, 30, 40])["drift"]
    assert len(out["buckets"]) == 2
    for axis, n in (("store_credentials", 2), ("elapsed", 2), ("store_bytes", 0)):
        assert out["slopes"]["p50"][axis] == {
            "measured": False,
            "reason": f"A slope's confidence interval needs at least three points, and {n} "
                      f"were given."}
    flat = soak_run(tmp_path, [10, 20, 30, 40, 50, 60], footprints=[(0, 5)])["drift"]
    assert flat["slopes"]["p50"]["store_bytes"]["measured"] is False
    assert flat["slopes"]["p50"]["store_bytes"]["reason"] == \
        "Every x is the same, so no slope can be fitted."


def test_a_bucket_with_no_completed_credential_is_left_out_of_the_fit(tmp_path):
    out = soak_run(tmp_path, [None, None, 10, 20, 30, 40, 50, 60])["drift"]
    assert out["buckets"][0]["p50_ns"] is None and out["buckets"][0]["credentials"] == 0
    assert out["slopes"]["p50"]["store_credentials"]["n"] == 3


def test_the_markdown_carries_the_drift_slopes_and_the_bucket_table(tmp_path):
    soak_run(tmp_path, [10, 30, 50, 70, 90, 110], footprints=[(0, 1000), (3 * S, 3000)])
    report.write_report(tmp_path, generator_commit="gc")
    text = (tmp_path / "report.md").read_text()
    assert "## Soak drift" in text
    fit = json.loads((tmp_path / "report.json").read_text())["drift"]["slopes"]["p50"]
    assert f"| p50 | per 10 credentials | {round(fit['store_credentials']['slope_ns'])} ns" \
        in text
    assert f"| 3 | 3.0 | 3000 | 2 | 0 | {hdr(50 * MS)} ns | {hdr(70 * MS)} ns |" in text
    soak_run(tmp_path, [10, 20, 30, 40])
    report.write_report(tmp_path, generator_commit="gc")
    assert "not measured: A slope's confidence interval needs at least three points" in \
        (tmp_path / "report.md").read_text()


def test_a_bucket_leaves_out_an_interval_it_cannot_place(tmp_path):
    # c1's verified is on a stack clock with no measured offset, so it is not measured.
    events = window(6 * S) + [ev("requested", i * S, [f"c{i}"]) for i in range(6)]
    events += [ev("verified", i * S + 10 * MS, [f"c{i}"], method="polled", resolution=500)
               for i in range(6) if i != 1]
    events.append(ev("verified", S + 10 * MS, ["c1"], clock=SK, resolution=500))
    out = derive(write_run(tmp_path, events, run=run_section(
        soak={"bucket": 2, "per_credentials": 10})))["drift"]
    assert [b["credentials"] for b in out["buckets"]] == [1, 2, 2]


def test_a_size_sample_older_than_two_intervals_is_not_used(tmp_path):
    # Samples every 1 s were meant; the last one arrived at 1 s. Buckets centre at 1, 3 and 5 s:
    # the first reads 1000; at 3 s the sample is 2 s old, still within two intervals; at 5 s
    # it is 4 s old, so that bucket has no size rather than a stale one.
    out = soak_run(tmp_path, [10, 30, 50, 70, 90, 110], footprints=[(0, 500), (S, 1000)],
                   sample_s=1.0)["drift"]
    assert [b["store_bytes"] for b in out["buckets"]] == [1000, 1000, None]


def test_a_preseed_that_lost_credentials_refuses_the_run(tmp_path):
    # The soak was declared to start from a store of 100; 4 never made it in, so its numbers
    # describe a store nobody asked for (hostile review of bench-baseline, finding 2).
    out = soak_run(tmp_path, [10, 30, 50, 70, 90, 110], preseed=(100, 4))
    assert out["refused"] is True
    assert out["refused_reason"] == ("The preseed asked for 100 credentials and 4 failed, so "
                                     "the soak did not start from the store it declared.")
    assert out["preseed"] == {"requested": 100, "failed": 4}
    assert any("preseed" in w for w in out["warnings"])
    clean = soak_run(tmp_path / "b", [10, 30, 50, 70, 90, 110], preseed=(100, 0))
    assert clean["refused"] is False and clean["preseed"] == {"requested": 100, "failed": 0}
    assert "preseed" not in derive(write_run(tmp_path / "c", window() + simple("c1", 0, 9)))


@pytest.mark.parametrize("preseed, declared, reason", [
    (None, 100, "The run declared a preseed of 100 credentials and its timeline records none, "
                "so the soak did not start from the store it declared."),
    ((10, 0), 100, "The run declared a preseed of 100 credentials and its timeline records 10 "
                   "requested, so the soak did not start from the store it declared."),
    (("100", 0), 100, "The preseed's counts are not whole numbers ('100' requested, 0 failed), "
                      "so the store the soak started from is unknown."),
])
def test_a_preseed_missing_short_or_malformed_refuses_the_run(tmp_path, preseed, declared,
                                                              reason):
    out = soak_run(tmp_path, [10, 30, 50, 70, 90, 110], preseed=preseed, declared=declared)
    assert out["refused"] is True and out["refused_reason"] == reason


def test_a_malformed_preseed_on_a_run_that_declared_none_is_refused_not_a_crash(tmp_path):
    events = window() + simple("c1", 0, 9) + [
        ev("preseed_ended", 0, attrs={"requested": 3, "failed": "0"})]
    out = derive(write_run(tmp_path, events))
    assert out["refused"] is True
    assert out["refused_reason"].startswith("The preseed's counts are not whole numbers")


def preseed_events(*ended, at=-S):
    return window(6 * S) + simple("c1", 0, 9) + [
        ev("preseed_ended", t, attrs={"requested": r, "failed": f}) for (r, f, t) in
        [(r, f, at) if len(x) == 2 else x for x in ended for (r, f) in [x[:2]]]]


@pytest.mark.parametrize("ended, declared, reason", [
    ([(100, 0, 7 * S)], 100, "The preseed finished after the measured window opened, so the "
                             "soak did not start from the store it declared."),
    ([(50, 0), (50, 0)], 100, "The timeline records 2 preseeds where a run has at most one, so "
                              "the store the soak started from is unknown."),
    ([(50, 0)], None, "The timeline records a preseed of 50 credentials the run never "
                      "declared, so the soak did not start from the store it declared."),
    ([(-5, 0)], -5, "The preseed's counts are not possible (-5 requested, 0 failed), so the "
                    "store the soak started from is unknown."),
    ([(5, 6)], 5, "The preseed's counts are not possible (5 requested, 6 failed), so the store "
                  "the soak started from is unknown."),
])
def test_a_preseed_late_repeated_undeclared_or_impossible_refuses_the_run(tmp_path, ended,
                                                                          declared, reason):
    extra = {"preseed": {"credentials": declared, "concurrency": 2}} if declared else {}
    out = derive(write_run(tmp_path, preseed_events(*ended), run=run_section(
        soak={"bucket": 2, "per_credentials": 10}, **extra)))
    assert out["refused"] is True and out["refused_reason"] == reason


def test_a_preseed_record_on_another_clock_refuses_the_run(tmp_path):
    events = window(6 * S) + simple("c1", 0, 9) + [
        ev("preseed_ended", -S, clock=SK, attrs={"requested": 5, "failed": 0})]
    out = derive(write_run(tmp_path, events, run=run_section(
        soak={"bucket": 2, "per_credentials": 10},
        preseed={"credentials": 5, "concurrency": 1})))
    assert out["refused"] is True
    assert out["refused_reason"] == ("The preseed's record is on the clock stack:imbu, not "
                                     "the harness's, so when it finished is unknown.")


# --- signer census and the HSM profile check (this.i 2p26rn9h) -------------------------------

MS_ = 1_000_000


def signer(ops, *, latency_ms=10.0, rate=None, responses=0, issuances=1):
    return {"latency_ms": latency_ms, "rate": rate, "operations": ops,
            "signed_responses": responses, "issuances": issuances}


def census_run(tmp_path, signers):
    return derive(write_run(tmp_path, window() + simple("c1", 0, 9),
                            census={"issuer": {"signers": signers}}))


def test_a_run_without_a_census_says_not_measured(tmp_path):
    assert derive(write_run(tmp_path, window() + simple("c1", 0, 9)))["census"] == \
        "not measured"


def test_the_census_counts_by_kind_and_sets_signatures_against_responses(tmp_path):
    # Latency 10 ms, no cap: each op starts when asked and finishes 10 ms (and a hair) later.
    ops = [["incept", 0, 0, 10 * MS_]] + [["sign", t, t, t + 10 * MS_ + 50_000]
                                          for t in range(0, 9 * 100 * MS_, 100 * MS_)]
    out = census_run(tmp_path, [signer(ops, responses=5, issuances=2)])["census"]
    home, = out["issuer"]["signers"]
    assert home["counts"] == {"incept": 1, "sign": 9}
    # 9 signatures: 5 answers to the adapter's requests and 1 the inception event's, which
    # leaves 3 for 2 credentials.
    assert (home["signed_responses"], home["other_signatures"], home["per_issuance"]) == \
        (5, 3, 1.5)
    assert home["latency"] == {"configured_ms": 10.0, "median_ms": 10.05, "ok": True}
    assert home["rate"] == {"configured_per_s": None, "exercised": False, "ok": True}
    assert home["ok"] is True and out["ok"] is True


def test_a_signer_slower_than_its_profile_by_more_than_5_percent_refuses_the_run(tmp_path):
    ops = [["sign", t, t, t + 11 * MS_] for t in range(0, 5 * 100 * MS_, 100 * MS_)]
    report = census_run(tmp_path, [signer(ops)])
    home, = report["census"]["issuer"]["signers"]
    assert home["latency"]["ok"] is False and report["census"]["ok"] is False
    assert report["refused"] is True
    assert report["refused_reason"] == ("The signer's profile check failed for issuer signer "
                                        "0: latency median 11.0 ms against 10.0 ms "
                                        "configured, more than 5% off.")


def test_with_no_latency_configured_the_median_must_stay_under_a_millisecond(tmp_path):
    fast = [["sign", 0, 0, 200_000], ["sign", 5, 5, 300_005]]
    home, = census_run(tmp_path, [signer(fast, latency_ms=None)])["census"]["issuer"]["signers"]
    assert home["latency"] == {"configured_ms": None, "median_ms": 0.25, "ok": True}
    slow = [["sign", 0, 0, 2 * MS_]]
    report = census_run(tmp_path / "b", [signer(slow, latency_ms=None)])
    assert report["refused"] is True
    assert "latency median 2.0 ms against none configured" in report["refused_reason"]


def test_the_cap_is_checked_on_the_starts_it_delayed(tmp_path):
    # Rate 10/s: three asked at once start 100 ms apart; the first did not wait.
    ops = [["sign", 0, 0, MS_], ["sign", 0, 100 * MS_, 101 * MS_],
           ["sign", 0, 200 * MS_, 201 * MS_], ["sign", 500 * MS_, 500 * MS_, 501 * MS_]]
    home, = census_run(tmp_path, [signer(ops, latency_ms=1.0, rate=10.0)])["census"][
        "issuer"]["signers"]
    assert home["rate"] == {"configured_per_s": 10.0, "exercised": True,
                            "median_spacing_ms": 100.0, "ok": True}
    tight = [["sign", 0, 0, MS_], ["sign", 0, 80 * MS_, 81 * MS_]]
    # Asked for 5 ms after the device was free again: late by the loop, not held by the cap.
    late = [["sign", 0, 0, MS_], ["sign", 105 * MS_, 105 * MS_ + 50_000, 106 * MS_]]
    home, = census_run(tmp_path / "d", [signer(late, latency_ms=1.0, rate=10.0)])["census"][
        "issuer"]["signers"]
    assert home["rate"]["exercised"] is False
    report = census_run(tmp_path / "b", [signer(tight, latency_ms=1.0, rate=10.0)])
    assert report["refused"] is True
    assert "spacing median 80.0 ms against 100.0 ms" in report["refused_reason"]
    idle = [["sign", 0, 0, MS_], ["sign", 900 * MS_, 900 * MS_, 901 * MS_]]
    home, = census_run(tmp_path / "c", [signer(idle, latency_ms=1.0, rate=10.0)])["census"][
        "issuer"]["signers"]
    assert home["rate"] == {"configured_per_s": 10.0, "exercised": False, "ok": True}


def test_a_signer_that_did_nothing_is_reported_and_passes_nothing(tmp_path):
    report = census_run(tmp_path, [signer([], issuances=0)])
    home, = report["census"]["issuer"]["signers"]
    assert home["counts"] == {} and home["per_issuance"] is None
    assert home["latency"] == {"configured_ms": 10.0, "median_ms": None, "ok": False}
    assert report["refused"] is True


def test_the_markdown_shows_the_census_and_its_check(tmp_path):
    ops = [["sign", t, t, t + 10 * MS_] for t in range(0, 300 * MS_, 100 * MS_)]
    census_run(tmp_path, [signer(ops, responses=1, issuances=1)])
    report.write_report(tmp_path, generator_commit="gc")
    text = (tmp_path / "report.md").read_text()
    assert "## Signer census" in text
    assert "| issuer | 0 | sign 3 | 1 | 2 | 2.0 | 10.0 ms (10.0 configured) ok | not exercised |" \
        in text


def test_what_else_an_adapter_counted_beside_its_signers_is_carried_into_the_report(tmp_path):
    pool = [{"home": 0, "registries": 200, "accepted_ns": 5, "finished_ns": 36_000_000_005}]
    out = derive(write_run(tmp_path, window() + simple("c1", 0, 9),
                           census={"issuer": {"signers": [], "pool": pool}}))["census"]
    assert out["issuer"] == {"signers": [], "pool": pool} and out["ok"] is True
