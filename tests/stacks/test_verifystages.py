"""The timed verifier's summary (this.i 3jeks5st): stage times, in the papers' terms."""

import json
import subprocess
import sys

import pytest

from keri_bench.stacks import stages, verifystages

MS = 1_000_000


def attempt(credential, *, outcome="accepted", overlapped=False, meet=0, code=None, **given):
    times = {**dict.fromkeys(stages.STAGES, 0), "meet": meet, **given}
    calls = {stage: int(ns > 0) for stage, ns in times.items()}
    total = sum(times.values()) + 2 * MS  # two ms the stages leave over
    return {"credential": credential, "outcome": outcome, "code": code, "total_ns": total,
            "overlapped": overlapped, "phases": {"admit": total // 2, "current": total // 2},
            "stages": times, "calls": calls}


WARM = dict(**{"key_state.issuer": 20 * MS, "key_state.holder": 10 * MS, "signatures": MS,
               "tel.fetch": 4 * MS, "tel.vet": 3 * MS, "receipt": MS})


def lines(*records):
    return [json.dumps(r) for r in records]


def test_warm_attempts_are_summarised_by_stage_and_by_the_papers_rollups():
    records = lines(
        {"credential": "lockbox-open", "outcome": "opened", "code": None, "total_ns": 90 * MS,
         "overlapped": False, "phases": {}, "stages": {**dict.fromkeys(stages.STAGES, 0),
                                                      "meet": 80 * MS},
         "calls": {**dict.fromkeys(stages.STAGES, 0), "meet": 1}},
        attempt("c0", meet=50 * MS, **WARM),          # the first presentation meets the holder
        attempt("c1", **WARM), attempt("c2", **WARM),
        attempt("c3", overlapped=True, **WARM),        # ran beside another: left out
        attempt("c4", outcome="refused", code="e.env.observer.gap.r", **WARM))
    summary = verifystages.summarise(records)
    assert summary["attempts"] == {"total": 5, "accepted": 4, "refused": 1, "overlapped": 1,
                                   "warm": 2, "refused_codes": {"e.env.observer.gap.r": 1}}
    stage = summary["warm"]["stages"]["key_state.issuer"]
    assert stage["n"] == 2 and stage["p50_ms"] == pytest.approx(20, rel=1e-3)
    assert stage["mean_ms"] == pytest.approx(20) and stage["calls_per_attempt"] == 1
    total = summary["warm"]["total"]
    assert total["mean_ms"] == pytest.approx(41)
    rollups = summary["warm"]["rollups"]
    assert rollups["issuer key state"]["mean_ms"] == pytest.approx(20)
    assert rollups["status"]["mean_ms"] == pytest.approx(7)
    assert rollups["holder binding"]["mean_ms"] == pytest.approx(11)
    assert rollups["admit receipt"]["mean_ms"] == pytest.approx(1)
    assert rollups["everything else"]["mean_ms"] == pytest.approx(2)
    assert sum(r["share"] for r in rollups.values()) == pytest.approx(1)
    assert summary["warm"]["phases"]["admit"]["mean_ms"] == pytest.approx(20.5)
    assert summary["meetings"] == {"lockbox-open": pytest.approx(80), "c0": pytest.approx(50)}


def test_nothing_warm_is_refused_rather_than_summarised_as_zero():
    with pytest.raises(verifystages.BenchError) as empty:
        verifystages.summarise(lines(attempt("c0", meet=MS, **WARM)))
    assert empty.value.code == verifystages.E_EMPTY


def test_a_line_that_is_not_a_record_is_refused_by_line_number():
    with pytest.raises(verifystages.BenchError) as bad:
        verifystages.summarise(lines(attempt("c1", **WARM)) + ["{not json"])
    assert bad.value.code == verifystages.E_FORMAT and "line 2" in bad.value.message


def test_the_markdown_names_each_rollup_and_the_left_out_attempts():
    summary = verifystages.summarise(lines(attempt("c1", **WARM), attempt("c2", **WARM)))
    text = verifystages.markdown(summary)
    for name in ("issuer key state", "status", "holder binding", "admit receipt",
                 "everything else", "key_state.issuer", "tel.fetch"):
        assert name in text
    assert "| total |" in text


def test_the_module_writes_json_and_markdown(tmp_path):
    source = tmp_path / "stages.jsonl"
    source.write_text("\n".join(lines(attempt("c1", **WARM), attempt("c2", **WARM))) + "\n")
    subprocess.run([sys.executable, "-m", "keri_bench.stacks.verifystages", str(source),
                    "--out", str(tmp_path / "out")], check=True)
    assert json.loads((tmp_path / "out" / "stages.json").read_text())["attempts"]["warm"] == 2
    assert "holder binding" in (tmp_path / "out" / "stages.md").read_text()


def test_blank_lines_are_skipped():
    summary = verifystages.summarise(lines(attempt("c1", **WARM)) + ["", "  "])
    assert summary["attempts"]["warm"] == 1


@pytest.mark.parametrize("broken", [
    {"outcome": "accepted"},                                    # no times at all
    {**attempt("c1", **WARM), "outcome": "maybe"},              # not an outcome the verifier writes
    {**attempt("c1", **WARM), "calls": {"meet": 0}},            # a stage's calls missing
    {k: v for k, v in attempt("c1", outcome="refused", **WARM).items() if k != "code"},
    {**attempt("c1", **WARM), "credential": 7},
    {**attempt("c1", **WARM), "holder": 7},
    {**attempt("c1", **WARM), "total_ns": True},                # a boolean is not a time
    {**attempt("c1", **WARM), "total_ns": -1},
    {**attempt("c1", **WARM), "phases": {"admit": 1}},          # accepted, but one call untimed
    {**attempt("c1", **WARM), "phases": {"admit": 1, "current": 1, "other": 1}},
    {**attempt("c1", **WARM), "phases": {"admit": -1, "current": 1}},
    {**attempt("c1", **WARM), "overlapped": 0},
    {**attempt("c1", **WARM), "phases": []},
    {**attempt("c1", **WARM), "stages": {**attempt("c1", **WARM)["stages"], "extra": 0}},
    {**attempt("c1", **WARM), "phases": {"admit": 10**12, "current": 10**12}},  # past total
    {**attempt("c1", **WARM), "stages": {**attempt("c1", **WARM)["stages"],
                                         "tel.vet": 10**12}},   # alone, yet past total
    {**attempt("c1", **WARM), "calls": {**attempt("c1", **WARM)["calls"], "tel.vet": 10**400}},
    {**attempt("c1", **WARM), "credential": "\ud800"},
    [1, 2]])
def test_a_record_missing_what_the_summary_reads_is_refused_by_line_number(broken):
    with pytest.raises(verifystages.BenchError) as bad:
        verifystages.summarise([json.dumps(broken)])
    assert bad.value.code == verifystages.E_FORMAT and "line 1" in bad.value.message


def test_a_meeting_names_the_holder_it_met():
    summary = verifystages.summarise(lines({**attempt("c0", meet=MS, **WARM), "holder": "Eh"},
                                           attempt("c1", **WARM)))
    assert summary["meetings"] == {"c0 (Eh)": pytest.approx(1)}


def test_stages_past_the_total_are_allowed_on_an_overlapped_attempt():
    record = {**attempt("c1", **WARM), "overlapped": True}
    record["stages"] = {**record["stages"], "tel.vet": 10**12}
    summary = verifystages.summarise(lines(record, attempt("c2", **WARM)))
    assert summary["attempts"]["overlapped"] == 1


def test_a_refusal_without_a_code_is_counted_under_a_name_that_sorts():
    summary = verifystages.summarise(lines(
        attempt("c1", **WARM), attempt("c2", outcome="refused", **WARM),
        attempt("c3", outcome="refused", code="e.env.observer.gap.r", **WARM)))
    assert summary["attempts"]["refused_codes"] == {verifystages.UNCODED: 1,
                                                    "e.env.observer.gap.r": 1}
    json.dumps(summary, sort_keys=True)
