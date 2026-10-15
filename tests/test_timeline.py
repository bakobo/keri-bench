"""The JSONL timeline: a header line, then one line per event (this.i 8q4myzru)."""

import json

import pytest

from keri_bench.errors import BenchError
from keri_bench.timeline import (TIMELINE_SCHEMA, Event, Header, TimelineWriter, read_timeline)

CLOCK = "monotonic@boot-1"


def event(**over):
    fields = dict(name="anchored", t=1_000, method="observed", clock=CLOCK, resolution=1,
                  credentials=("c1",), role="issuer")
    fields.update(over)
    return Event(**fields)


def test_a_written_timeline_reads_back_identically(tmp_path):
    path = tmp_path / "timeline.jsonl"
    header = Header(harness_commit="abc123", harness_clock=CLOCK)
    batch = event(name="published", role="state_distribution", credentials=("c1", "c2", "c3"),
                  batch_id="b7", method="polled", resolution=50_000_000,
                  attrs={"digest": "E123"})
    with TimelineWriter(path, header) as sink:
        sink.write(event())
        sink.write(batch)
    read_header, events = read_timeline(path)
    assert read_header == header
    assert events == [event(), batch]
    first = json.loads(path.read_text().splitlines()[0])
    assert first == {"timeline_schema": TIMELINE_SCHEMA, "vocabulary_version": 1,
                     "harness_commit": "abc123", "harness_clock": CLOCK}


def test_one_batch_event_is_one_line_naming_every_credential(tmp_path):
    path = tmp_path / "timeline.jsonl"
    with TimelineWriter(path, Header(harness_commit="x", harness_clock=CLOCK)) as sink:
        sink.write(event(credentials=("a", "b", "c"), batch_id="b1"))
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["credentials"] == ["a", "b", "c"]


def test_optional_fields_are_omitted_when_absent(tmp_path):
    line = event().to_json()
    assert "batch_id" not in line and "attrs" not in line


@pytest.mark.parametrize("over, code", [
    ({"name": "teleported"}, "e.input.range.event.name.f"),
    ({"method": "guessed"}, "e.input.range.event.method.f"),
    ({"clock": ""}, "e.input.missing.event.clock.f"),
    ({"method": "reported", "clock": ""}, "e.input.missing.event.clock.f"),
    ({"resolution": -1}, "e.input.range.event.resolution.f"),
    ({"method": "polled", "resolution": 0}, "e.input.range.event.resolution.f"),
    ({"t": 1.5}, "e.input.format.event.f"),
    ({"credentials": ("c1", 2)}, "e.input.format.event.f"),
    ({"role": "bystander"}, "e.input.range.event.role.f"),
    ({"name": "witnessed", "role": "issuer"}, "e.input.range.event.role.f"),
    ({"name": "dispatched", "role": "issuer"}, "e.input.range.event.role.f"),
])
def test_a_malformed_event_is_refused_with_a_code(over, code):
    with pytest.raises(BenchError) as refused:
        event(**over)
    assert refused.value.code == code


def test_harness_events_belong_to_the_harness():
    assert event(name="dispatched", role="harness", credentials=("c1",)).role == "harness"


def test_an_unknown_timeline_schema_is_refused(tmp_path):
    path = tmp_path / "timeline.jsonl"
    path.write_text(json.dumps({"timeline_schema": 99, "vocabulary_version": 1,
                                "harness_commit": "x", "harness_clock": CLOCK}) + "\n")
    with pytest.raises(BenchError) as refused:
        read_timeline(path)
    assert refused.value.code == "e.feature.unsupported.timeline.schema.f"


@pytest.mark.parametrize("content", [
    "",
    "not json\n",
    "[1, 2]\n",
    json.dumps({"timeline_schema": 1}) + "\n",
])
def test_a_timeline_without_a_well_formed_header_is_refused(tmp_path, content):
    path = tmp_path / "timeline.jsonl"
    path.write_text(content)
    with pytest.raises(BenchError) as refused:
        read_timeline(path)
    assert refused.value.code.startswith("e.input.")


def test_a_malformed_event_line_names_its_line_number(tmp_path):
    path = tmp_path / "timeline.jsonl"
    with TimelineWriter(path, Header(harness_commit="x", harness_clock=CLOCK)) as sink:
        sink.write(event())
    with open(path, "a") as more:
        more.write('{"name": "anchored"}\n')
        more.write("\n")
    with pytest.raises(BenchError) as refused:
        read_timeline(path)
    assert refused.value.message.startswith("Line 3 of ")


def test_a_non_object_event_line_is_refused(tmp_path):
    path = tmp_path / "timeline.jsonl"
    with TimelineWriter(path, Header(harness_commit="x", harness_clock=CLOCK)):
        pass
    with open(path, "a") as more:
        more.write("[]\n")
    with pytest.raises(BenchError) as refused:
        read_timeline(path)
    assert refused.value.code == "e.input.format.timeline.line.f"


def test_an_oversized_line_is_refused_before_it_is_parsed(tmp_path, monkeypatch):
    import keri_bench.timeline as timeline
    monkeypatch.setattr(timeline, "MAX_LINE", 64)
    path = tmp_path / "timeline.jsonl"
    path.write_text(json.dumps({"timeline_schema": 1, "vocabulary_version": 1,
                                "harness_commit": "x" * 100, "harness_clock": CLOCK}) + "\n")
    with pytest.raises(BenchError) as refused:
        read_timeline(path)
    assert refused.value.code == "e.input.range.timeline.line.f"


def test_events_round_trip_through_a_dict():
    e = event(attrs={"said": "E1"}, batch_id="b")
    assert Event.from_dict(json.loads(e.to_json())) == e


def test_blank_lines_between_events_are_skipped(tmp_path):
    path = tmp_path / "timeline.jsonl"
    with TimelineWriter(path, Header(harness_commit="x", harness_clock=CLOCK)) as sink:
        sink.write(event())
    with open(path, "a") as more:
        more.write("\n" + event(t=2_000).to_json() + "\n")
    assert [e.t for e in read_timeline(path)[1]] == [1_000, 2_000]
