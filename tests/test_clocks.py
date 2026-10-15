"""Clock domains, measured offsets, and intervals (this.i 7vsky4sk, 98rxw488; Part B #4)."""

import pytest

from keri_bench import clocks
from keri_bench.clocks import (Clocks, Interval, NotMeasured, Offset, OffsetSample, combine,
                               interval, sample_offset)
from keri_bench.errors import BenchError
from keri_bench.timeline import Event

H = "monotonic@boot-1"
W = "realtime@host-1"


def ev(name, t, clock=H, method="observed", resolution=1, role=None):
    from keri_bench.vocabulary import MILESTONES
    return Event(name=name, t=t, method=method, clock=clock, resolution=resolution,
                 credentials=("c1",), role=role or MILESTONES.get(name, "harness"))


def offset_event(domain, offset, bracket):
    return Event(name="clock_offset", t=0, method="observed", clock=H, resolution=1,
                 credentials=(), role="harness",
                 attrs={"domain": domain, "offset_ns": offset, "bracket_ns": bracket})


def test_the_harness_domain_is_named_by_boot_id(tmp_path):
    boot = tmp_path / "boot_id"
    boot.write_text("abc-123\n")
    assert clocks.harness_domain(boot) == "monotonic@abc-123"
    assert clocks.realtime_domain().startswith("realtime@")


def test_a_missing_boot_id_is_an_environment_error(tmp_path):
    with pytest.raises(BenchError) as refused:
        clocks.harness_domain(tmp_path / "absent")
    assert refused.value.code == "e.env.clock.boot-id.f"


def test_an_offset_sample_brackets_the_foreign_read():
    reads = iter([100, 140])
    sample = sample_offset(W, read_domain=lambda: 5_000, mono=lambda: next(reads))
    assert sample == OffsetSample(domain=W, offset_ns=120 - 5_000, bracket_ns=40)


async def test_an_async_offset_sample_brackets_the_foreign_read():
    reads = iter([100, 160])

    async def read():
        return 1_000

    sample = await clocks.sample_offset_async(W, read, mono=lambda: next(reads))
    assert sample == OffsetSample(domain=W, offset_ns=130 - 1_000, bracket_ns=60)


def test_combining_samples_widens_the_bound_by_drift_between_them():
    combined = combine([OffsetSample(W, 1_000, 10), OffsetSample(W, 1_040, 30)])
    # Midpoint of the offsets; bound is the wider bracket plus the change between samples.
    assert combined == Offset(domain=W, offset_ns=1_020, bound_ns=30 + 40)


def test_combining_refuses_mixed_domains_and_no_samples():
    with pytest.raises(BenchError):
        combine([])
    with pytest.raises(BenchError):
        combine([OffsetSample(W, 1, 1), OffsetSample("other", 1, 1)])


def test_a_same_domain_interval_is_the_difference_at_the_coarser_resolution():
    got = interval(ev("anchored", 1_000), ev("witnessed", 4_000, method="polled",
                                             resolution=500), Clocks(H))
    assert got == Interval(value_ns=3_000, resolution_ns=500)


def test_a_cross_domain_interval_without_a_measured_offset_is_not_measured():
    got = interval(ev("anchored", 1_000, clock=W, method="reported"), ev("witnessed", 4_000),
                   Clocks(H))
    assert isinstance(got, NotMeasured)
    assert W in got.reason


def test_a_cross_domain_interval_carries_the_offset_bound_into_its_resolution():
    clock_map = Clocks.from_events(H, [offset_event(W, 10_000, 100), offset_event(W, 10_000, 50)])
    got = interval(ev("anchored", 1_000, clock=W, method="reported", resolution=1_000),
                   ev("witnessed", 14_000), clock_map)
    assert got == Interval(value_ns=14_000 - (1_000 + 10_000), resolution_ns=1_000 + 100)


def test_two_foreign_domains_each_add_their_bound():
    other = "realtime@host-2"
    clock_map = Clocks(H, {W: Offset(W, 0, 7), other: Offset(other, 500, 9)})
    got = interval(ev("anchored", 100, clock=W, method="reported"),
                   ev("witnessed", 100, clock=other, method="reported"), clock_map)
    assert got == Interval(value_ns=500, resolution_ns=1 + 7 + 9)


def test_events_in_the_same_foreign_domain_need_no_offset():
    got = interval(ev("anchored", 100, clock=W, method="reported"),
                   ev("constructed", 300, clock=W, method="reported", role="issuer"), Clocks(H))
    assert got == Interval(value_ns=200, resolution_ns=1)


@pytest.mark.parametrize("value, resolution, shown", [
    (3_000, 1, 3_000),
    (3_001, 500, 3_500),
    (3_000, 500, 3_000),
    (1, 50_000_000, 50_000_000),
    (-20, 500, 0),
])
def test_an_interval_is_never_reported_finer_than_its_resolution(value, resolution, shown):
    assert Interval(value, resolution).reported() == shown


def test_an_end_stamped_well_before_its_start_is_not_measured():
    got = interval(ev("requested", 100), ev("constructed", 50, role="issuer"), Clocks(H))
    assert isinstance(got, NotMeasured) and "inconsistent" in got.reason
    within = interval(ev("requested", 100), ev("constructed", 60, method="polled",
                                                 resolution=50, role="issuer"), Clocks(H))
    assert within == Interval(-40, 50) and within.reported() == 0


def test_offset_events_must_name_their_domain():
    bad = Event(name="clock_offset", t=0, method="observed", clock=H, resolution=1,
                credentials=(), role="harness", attrs={"offset_ns": 1})
    with pytest.raises(BenchError) as refused:
        Clocks.from_events(H, [bad])
    assert refused.value.code == "e.input.format.event.clock-offset.f"


def test_other_events_are_ignored_when_building_the_clock_map():
    assert Clocks.from_events(H, [ev("anchored", 1)]).offsets == {}
