"""Reconciliation: done, resumed after a fault, or lost, and the double effects (Part B #9)."""

import pytest

from keri_bench.errors import BenchError
from keri_bench.reconcile import Outcome, classify, summary
from keri_bench.timeline import Event
from keri_bench.vocabulary import MILESTONES

H = "monotonic@boot-1"
STACK = "stack:imbu"


def ev(name, t, creds, clock=H, said=None, method="observed"):
    attrs = {"said": said} if said is not None else None
    return Event(name=name, t=t, method=method, clock=clock, resolution=1,
                 credentials=tuple(creds), role=MILESTONES.get(name, "harness"), attrs=attrs)


FAULT = (1000, 2000)

EVENTS = [
    # c-done: whole lifecycle before the fault.
    ev("requested", 100, ["c-done"]),
    ev("constructed", 150, ["c-done"], said="Esaid-done"),
    ev("verified", 300, ["c-done"]),
    # c-resumed: requested before the fault, its last harness-clock event inside it.
    ev("requested", 900, ["c-resumed"]),
    ev("verified", 1100, ["c-resumed"]),
    # c-late: its stack-clock event lands inside the fault window by number, but only
    # harness-clock events count, so its lifecycle (2100..2200) misses the fault.
    ev("requested", 2100, ["c-late"]),
    ev("anchored", 1500, ["c-late"], clock=STACK, method="reported"),
    ev("verified", 2200, ["c-late"]),
    # c-lost: the verifier never heard of it.
    ev("requested", 400, ["c-lost"]),
    # c-unknown: the verifier says unknown.
    ev("requested", 410, ["c-unknown"]),
    # c-double: anchored twice per the witness pool, and two SAIDs across events.
    ev("requested", 420, ["c-double"]),
    ev("constructed", 430, ["c-double"], said="Esaid-a"),
    ev("anchored", 440, ["c-double"], said="Esaid-b"),
    # c-orphan: no requested event at all, so its lifecycle cannot be placed in time.
    ev("verified", 1500, ["c-orphan"]),
    # a harness event that covers no credential is ignored.
    ev("run_started", 0, []),
]

VERIFIER = {
    "c-done": {"status": "valid", "said": "Esaid-done"},
    "c-resumed": {"status": "revoked", "said": None},
    "c-late": {"status": "valid", "said": None},
    "c-unknown": {"status": "unknown", "said": None},
    "c-double": {"status": "valid", "said": "Esaid-a"},
    "c-orphan": {"status": "valid", "said": "Esaid-orphan"},
}

ANCHORS = {"c-done": 1, "c-resumed": None, "c-double": 2}


def outcomes():
    return classify(EVENTS, VERIFIER, ANCHORS, faults=[FAULT])


def test_every_credential_in_the_timeline_gets_exactly_one_outcome():
    assert set(outcomes()) == {"c-done", "c-resumed", "c-late", "c-lost", "c-unknown",
                               "c-double", "c-orphan"}


def test_a_verified_credential_whose_lifecycle_misses_every_fault_is_done():
    got = outcomes()
    assert got["c-done"] == Outcome("done", double_anchored=False, two_saids=False,
                                    saids=("Esaid-done",))
    assert got["c-late"].fate == "done"
    assert got["c-double"].fate == "done"


def test_a_done_credential_whose_lifecycle_overlaps_a_fault_is_resumed():
    assert outcomes()["c-resumed"].fate == "resumed"


def test_the_overlap_is_strict_at_both_edges():
    touching = [ev("requested", 0, ["a"]), ev("verified", 1000, ["a"]),
                ev("requested", 2000, ["b"]), ev("verified", 2500, ["b"])]
    got = classify(touching, {"a": {"status": "valid", "said": None},
                              "b": {"status": "valid", "said": None}}, {}, faults=[FAULT])
    assert got["a"].fate == "done" and got["b"].fate == "done"


def test_unknown_or_unanswered_credentials_are_lost():
    got = outcomes()
    assert got["c-lost"].fate == "lost"
    assert got["c-unknown"].fate == "lost"


def test_a_credential_with_no_requested_event_cannot_be_resumed():
    assert outcomes()["c-orphan"].fate == "done"


def test_more_than_one_anchor_flags_a_double_anchor():
    got = outcomes()
    assert got["c-double"].double_anchored
    assert not got["c-done"].double_anchored
    assert not got["c-resumed"].double_anchored  # None: the adapter cannot tell
    assert not got["c-lost"].double_anchored  # absent from the anchors map


def test_two_distinct_saids_across_events_and_the_verifier_flag_two_saids():
    got = outcomes()
    assert got["c-double"].two_saids
    assert got["c-double"].saids == ("Esaid-a", "Esaid-b")
    assert not got["c-done"].two_saids  # the same SAID twice is one SAID
    assert got["c-orphan"].saids == ("Esaid-orphan",)
    assert got["c-lost"].saids == ()


def test_the_verifier_alone_can_supply_the_second_said():
    events = [ev("requested", 0, ["x"]), ev("constructed", 5, ["x"], said="E1")]
    got = classify(events, {"x": {"status": "valid", "said": "E2"}}, {}, faults=[])
    assert got["x"].two_saids and got["x"].saids == ("E1", "E2")


def test_a_status_outside_the_three_is_refused():
    with pytest.raises(BenchError) as refused:
        classify([ev("requested", 0, ["x"])], {"x": {"status": "pending", "said": None}}, {},
                 faults=[])
    assert refused.value.code == "e.input.range.reconcile.status.f"
    assert "pending" in refused.value.message


def test_the_summary_counts_each_fate_and_each_flag():
    assert summary(outcomes()) == {"total": 7, "done": 4, "resumed": 1, "lost": 2,
                                   "double_anchored": 1, "two_saids": 1}


def test_the_summary_of_nothing_is_all_zeros():
    assert summary({}) == {"total": 0, "done": 0, "resumed": 0, "lost": 0,
                           "double_anchored": 0, "two_saids": 0}


def test_an_outcome_serialises_to_plain_json():
    assert outcomes()["c-double"].to_dict() == {"fate": "done", "double_anchored": True,
                                                "two_saids": True,
                                                "saids": ["Esaid-a", "Esaid-b"]}


def test_ids_that_are_not_requested_credentials_are_not_classified():
    from keri_bench.timeline import Event
    sync = Event(name="sync_full_done", t=5, method="observed", clock="monotonic@b",
                 resolution=1, credentials=("sync-cold",), role="verifier")
    assert classify([sync], {}, {}, faults=[]) == {}
