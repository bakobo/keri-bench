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
    assert got["driver"] == {"ok": True, "p99_lag_ns": 7, "backlog": [1], "reason": None,
                             "lag_limit_ns": 5 * MS}


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


def test_a_negative_interval_clamps_to_zero(tmp_path):
    events = window() + simple("c1", 100, 1000) + [
        ev("constructed", 300, ["c1"]), ev("registry_updated", 250, ["c1"])]
    got = derive(write_run(tmp_path, events, run=run_section(
        measure=["constructed", "registry_updated"])))
    assert got["intervals"]["constructed->registry_updated"]["max"] == 0


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
    assert pairs["construction_vs_registry"] == {"measurable": True, "gap": None}
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
    assert got["observability"]["pairs"]["construction_vs_registry"]["measurable"] is True


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
              + [ev("requested", 9000, ["c3"]), ev("constructed", 500, ["c1"]),
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
                                                    + [ev("requested", 100, ["c1"])])))
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
                    "throughput", "faults", "reconciliation", "measurement_cost", "warnings"):
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
