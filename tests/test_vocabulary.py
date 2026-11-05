"""The lifecycle vocabulary the harness owns (this.i 7wt73dlu; test plan, Milestones and Roles)."""

from keri_bench import vocabulary as v


def test_every_milestone_in_the_plan_is_named_once_and_owned_by_one_role():
    expected = {
        "issuer": {"requested", "accepted", "constructed", "registry_updated", "anchored",
                   "grant_sent", "revocation_requested", "revocation_anchored"},
        "witness_pool": {"witnessed", "revocation_witnessed"},
        "state_distribution": {"published", "revocation_published"},
        "holder": {"delivered", "stored", "proof_generated"},
        "verifier": {"verified", "revocation_recognised", "sync_full_started", "sync_full_done",
                     "sync_increment_applied"},
    }
    for role, names in expected.items():
        assert v.milestones_of(v.Role(role)) == names
    assert set(v.MILESTONES) == set().union(*expected.values())


def test_milestone_names_are_snake_case_and_never_reuse_a_reserved_name():
    for name in v.MILESTONES:
        assert name.isidentifier() and name == name.lower()
    assert not set(v.MILESTONES) & v.RESERVED


def test_harness_events_are_separate_from_milestones():
    assert not set(v.HARNESS_EVENTS) & set(v.MILESTONES)
    assert {"dispatched", "clock_offset", "fault_started", "fault_ended", "process_killed",
            "process_ready", "process_exited", "preseed_started", "preseed_ended", "footprint",
            "footprint_failed", "stop_reached"} <= set(v.HARNESS_EVENTS)


def test_the_seven_pairs_quote_utah_and_name_only_known_milestones():
    assert len(v.PAIRS) == 7
    for pair in v.PAIRS:
        assert not pair.utah.startswith(("\u201c", '"'))  # stored without quotation marks
        for start, end in pair.intervals:
            assert start in v.MILESTONES and end in v.MILESTONES
    first = v.PAIRS[0]
    assert first.utah == ("Credential construction throughput from registry update throughput.")
    assert first.throughput == ("constructed", "registry_updated")


def test_a_pair_lists_every_milestone_it_needs():
    pair = next(p for p in v.PAIRS if p.key == "anchor_vs_witness")
    assert pair.milestones() == {"registry_updated", "anchored", "witnessed"}
    throughput = v.PAIRS[0]
    assert throughput.milestones() == {"constructed", "registry_updated"}


def test_the_fault_pair_has_no_intervals_of_its_own():
    fault = v.PAIRS[-1]
    assert fault.key == "normal_vs_faulted" and fault.intervals == () and fault.throughput is None
    assert fault.milestones() == set()
    # Verbatim, including the missing full stop in Utah's list.
    assert fault.utah.endswith("duplicate messages, and recovery")


def test_methods_are_the_three_the_plan_names():
    assert set(v.Method) == {"observed", "polled", "reported"}


def test_the_lifecycle_chains_name_every_milestone_but_the_sync_ones_once():
    chained = v.ISSUANCE_CHAIN + v.REVOCATION_CHAIN
    assert len(chained) == len(set(chained))
    assert set(v.MILESTONES) - set(chained) == {"sync_full_started", "sync_full_done",
                                                "sync_increment_applied"}
