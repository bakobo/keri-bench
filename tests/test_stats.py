"""Statistics (test plan, Statistics and Soak; Part B #3).

Every expected value here is computed by hand in the test, never by the code under test.
"""

import math
from fractions import Fraction

import pytest

from keri_bench import stats
from keri_bench.errors import BenchError
from keri_bench.stats import (Drift, Histogram, Saturation, Spread, drift, open_loop_latencies,
                              saturation, spread)


def refused(code, fn, *args, **kw):
    with pytest.raises(BenchError) as info:
        fn(*args, **kw)
    assert info.value.code == code
    assert info.value.message.endswith(".")
    return info.value


# --- Histogram: bucket boundaries -----------------------------------------------------------

# Hand layout: 2048 sub-buckets. Values below 2048 are exact. A value v >= 2048 lies in bucket
# b = bit_length(v) - 11, whose width is 2**b, so its upper bound is v rounded down to a multiple
# of 2**b, plus 2**b - 1.
@pytest.mark.parametrize("value, upper", [
    (0, 0),
    (1, 1),
    (2047, 2047),            # last exact value
    (2048, 2049),            # bucket 1, width 2: [2048, 2049]
    (2049, 2049),
    (2050, 2051),
    (4095, 4095),            # bucket 1's last slot: [4094, 4095]
    (4096, 4099),            # bucket 2, width 4: [4096, 4099]
    (4099, 4099),
    (4100, 4103),
    (8192, 8199),            # bucket 3, width 8
    (1_000_000, 1_000_447),  # bit_length 20 -> bucket 9, width 512; 1_000_000 // 512 = 1953
    (2**62, 2**62 + 2**52 - 1),       # bit_length 63 -> bucket 52
    (2**63 - 1, 2**63 - 1),           # the top of the range is its own bucket's upper bound
])
def test_upper_bound_at_bucket_boundaries(value, upper):
    assert Histogram.upper_bound(value) == upper


def test_three_significant_digits_hold_across_the_range():
    for v in (2048, 99_999, 10**9, 2**62):
        assert (Histogram.upper_bound(v) - v) / v < 1e-3


def test_percentile_quotes_the_bucket_upper_bound():
    h = Histogram()
    h.record(2048)
    assert h.percentile(100) == 2049
    h2 = Histogram()
    h2.record(2047)
    assert h2.percentile(50) == 2047


def test_percentile_uses_nearest_rank():
    h = Histogram()
    for v in (10, 20, 30, 40):
        h.record(v)
    # rank = ceil(p/100 * 4)
    assert h.percentile(25) == 10      # rank 1
    assert h.percentile(25.1) == 20    # rank ceil(1.004) = 2
    assert h.percentile(50) == 20      # rank 2
    assert h.percentile(75) == 30
    assert h.percentile(100) == 40
    assert h.percentile(0.001) == 10   # rank 1


def test_percentile_rank_is_exact_for_decimal_p():
    # 99.9% of 1000 is rank 999 exactly; binary float arithmetic would give 999.0000000000001
    # and round up to 1000.
    h = Histogram()
    h.record(1, count=999)
    h.record(2000)
    assert h.percentile(99.9) == 1
    assert h.percentile(Fraction(9991, 100)) == 2000


def test_count_with_repeats_and_min_max():
    h = Histogram()
    h.record(5000, count=3)
    h.record(7)
    assert h.count == 4
    assert h.min == 7
    assert h.max == 5000
    assert h.percentile(25) == 7
    assert h.percentile(26) == 5000 // 4 * 4 + 3   # bucket 2, width 4


def test_merge_adds_counts_and_extremes():
    a, b = Histogram(), Histogram()
    a.record(100, count=2)
    b.record(4096)
    b.record(3)
    a.merge(b)
    assert a.count == 4
    assert a.min == 3
    assert a.max == 4096
    assert a.percentile(100) == 4099
    assert a.percentile(50) == 100
    assert b.count == 2   # the source is not changed


def test_merging_an_empty_histogram_changes_nothing():
    a = Histogram()
    a.record(9)
    a.merge(Histogram())
    assert (a.count, a.min, a.max) == (1, 9, 9)
    empty = Histogram()
    empty.merge(a)
    assert (empty.count, empty.min, empty.max) == (1, 9, 9)


@pytest.mark.parametrize("bad", [-1, 1.5, "3", True, 2**63])
def test_record_refuses_a_value_out_of_range_or_type(bad):
    refused(stats.E_HIST_VALUE, Histogram().record, bad)


@pytest.mark.parametrize("bad", [0, -2, 1.0, False])
def test_record_refuses_a_bad_count(bad):
    refused(stats.E_HIST_COUNT, Histogram().record, 1, count=bad)


@pytest.mark.parametrize("bad", [-1, 2**63, 0.5])
def test_upper_bound_refuses_a_value_out_of_range(bad):
    refused(stats.E_HIST_VALUE, Histogram.upper_bound, bad)


@pytest.mark.parametrize("p", [0, -1, 100.0001, 101, "50", None, math.nan])
def test_percentile_refuses_p_outside_0_100(p):
    h = Histogram()
    h.record(1)
    refused(stats.E_HIST_PERCENTILE, h.percentile, p)


def test_an_empty_histogram_has_no_percentile_min_or_max():
    h = Histogram()
    assert h.count == 0
    refused(stats.E_HIST_EMPTY, h.percentile, 50)
    refused(stats.E_HIST_EMPTY, lambda: h.min)
    refused(stats.E_HIST_EMPTY, lambda: h.max)


def test_merge_refuses_a_non_histogram():
    refused(stats.E_HIST_MERGE, Histogram().merge, [1, 2])


# --- Coordinated omission -------------------------------------------------------------------

MS = 1_000_000


def stalled_run(delta, n, stall_start, stall_len):
    """Requests every delta ns, zero service time, nothing completes in [T, T+D) and the queue
    drains at T+D. Built here, independently of the code under test."""
    scheduled = [i * delta for i in range(n)]
    end = stall_start + stall_len
    completed = [end if stall_start <= t < end else t for t in scheduled]
    return scheduled, completed


@pytest.mark.parametrize("x", [50 * MS, 49 * MS, 20 * MS + MS // 2, 1, 1 * MS])
def test_a_stall_on_the_grid_adds_exactly_the_expected_samples(x):
    delta, D = 1 * MS, 50 * MS
    scheduled, completed = stalled_run(delta, 200, 100 * MS, D)
    lat = open_loop_latencies(scheduled, completed)
    expected = (D - x) // delta + 1
    assert sum(1 for v in lat if v >= x) == expected
    # Hand values: x=D -> 1 sample; D-delta -> 2; 20.5 ms -> floor(29.5)+1 = 30; 1 ns -> 50.
    assert expected == {50 * MS: 1, 49 * MS: 2, 20 * MS + MS // 2: 30, 1: 50, 1 * MS: 50}[x]


def test_a_stall_in_the_histogram_shows_in_the_tail():
    delta, D, n = 1 * MS, 50 * MS, 200
    scheduled, completed = stalled_run(delta, n, 100 * MS, D)
    h = Histogram()
    for v in open_loop_latencies(scheduled, completed):
        h.record(v)
    assert h.count == n
    assert h.max == D
    # One sample at D: rank 200 is in D's bucket, rank 199 (49 ms) is below D.
    assert h.percentile(100) >= D
    assert h.percentile(99.5) < D
    # 150 samples are zero: ranks 1..150.
    assert h.percentile(75) == 0
    assert h.percentile(75.5) > 0


def test_a_stall_off_the_grid_puts_no_sample_at_D():
    delta, D = 1 * MS, 50 * MS
    T = 100 * MS + MS // 2
    scheduled, completed = stalled_run(delta, 200, T, D)
    lat = open_loop_latencies(scheduled, completed)
    # First stalled request is at 101 ms, waiting D - 0.5 ms; last at 150 ms, waiting 0.5 ms.
    assert max(lat) == D - MS // 2
    assert sum(1 for v in lat if v >= D) == 0
    assert sum(1 for v in lat if v > 0) == 50


def test_open_loop_latencies_refuses_mismatched_lengths():
    refused(stats.E_LATENCY_LENGTH, open_loop_latencies, [0, 1], [0])


def test_open_loop_latencies_refuses_completion_before_schedule():
    refused(stats.E_LATENCY_NEGATIVE, open_loop_latencies, [0, 10], [5, 9])


# --- Saturation -----------------------------------------------------------------------------

def test_no_saturation_when_throughput_keeps_rising():
    steps = [(100, 100.0, 1000), (200, 200.0, 1000), (300, 300.0, 1100)]
    assert saturation(steps) is None


def test_throughput_rise_just_below_epsilon_saturates():
    # 1000 * 1.02 = 1020; 1019.9 is a rise of 1.99%.
    steps = [(100, 1000.0, 1000), (200, 1019.9, 1000)]
    assert saturation(steps) == Saturation(index=1, offered=200, reason="throughput")


def test_throughput_rise_just_above_epsilon_does_not_saturate():
    steps = [(100, 1000.0, 1000), (200, 1020.1, 1000)]
    assert saturation(steps) is None


def test_throughput_falling_saturates_at_the_first_falling_step():
    steps = [(100, 100.0, 10), (200, 200.0, 10), (300, 150.0, 10), (400, 100.0, 10)]
    assert saturation(steps) == Saturation(index=2, offered=300, reason="throughput")


def test_p99_just_above_k_saturates():
    steps = [(100, 100.0, 1000), (200, 200.0, 5001)]
    assert saturation(steps) == Saturation(index=1, offered=200, reason="latency")


def test_p99_at_exactly_k_does_not_saturate():
    steps = [(100, 100.0, 1000), (200, 200.0, 5000)]
    assert saturation(steps) is None


def test_throughput_is_reported_when_both_trip_at_once():
    steps = [(100, 100.0, 1000), (200, 100.0, 9000)]
    assert saturation(steps).reason == "throughput"


def test_scenario_overrides_epsilon_and_k():
    steps = [(100, 1000.0, 1000), (200, 1040.0, 1000), (300, 2000.0, 2500)]
    assert saturation(steps) is None
    assert saturation(steps, epsilon=0.05) == Saturation(1, 200, "throughput")
    assert saturation(steps, k=2.0) == Saturation(2, 300, "latency")


def test_saturation_refuses_fewer_than_two_steps():
    refused(stats.E_SAT_STEPS, saturation, [(100, 100.0, 1)])
    refused(stats.E_SAT_STEPS, saturation, [])


@pytest.mark.parametrize("steps", [
    [(100, 1.0, 1), (100, 2.0, 1)],
    [(200, 1.0, 1), (100, 2.0, 1)],
    [(100, 1.0, 1), (200, 2.0, 1), (150, 3.0, 1)],
])
def test_saturation_refuses_offered_load_that_does_not_increase(steps):
    refused(stats.E_SAT_ORDER, saturation, steps)


# --- Drift ----------------------------------------------------------------------------------

def test_drift_matches_a_hand_computed_example():
    xs, ys = [0, 1, 2, 3], [0, 1, 3, 3]
    # xbar 1.5, ybar 1.75; Sxx = 5, Sxy = 5.5 -> slope 1.1, intercept 0.1.
    # Fitted 0.1, 1.2, 2.3, 3.4; residuals -0.1, -0.2, 0.7, -0.4; SSE 0.70; s^2 = 0.35 (df 2).
    # SE = sqrt(0.35 / 5) = sqrt(0.07); t(0.975, 2) = 4.303.
    half = 4.303 * math.sqrt(0.07)
    d = drift(xs, ys)
    assert d.n == 4
    assert d.slope == pytest.approx(1.1)
    assert d.low == pytest.approx(1.1 - half)
    assert d.high == pytest.approx(1.1 + half)
    assert d.low == pytest.approx(-0.03847, abs=1e-5)


def test_a_perfect_line_has_a_zero_width_interval():
    d = drift([1, 2, 3, 4, 5], [7, 9, 11, 13, 15])
    assert d == Drift(slope=pytest.approx(2.0), low=pytest.approx(2.0), high=pytest.approx(2.0),
                      n=5)


def _alternating(n):
    """xs 0..n-1 and ys = xs + (+1, -1, +1, ...), so the interval's half-width over SE is t."""
    xs = list(range(n))
    ys = [x + (1 if i % 2 == 0 else -1) for i, x in enumerate(xs)]
    return xs, ys


def _t_used(n):
    xs, ys = _alternating(n)
    d = drift(xs, ys)
    xbar, ybar = sum(xs) / n, sum(ys) / n
    sxx = sum((x - xbar) ** 2 for x in xs)
    slope = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys)) / sxx
    sse = sum((y - (ybar + slope * (x - xbar))) ** 2 for x, y in zip(xs, ys))
    se = math.sqrt(sse / (n - 2) / sxx)
    return (d.high - d.slope) / se


@pytest.mark.parametrize("n, t", [
    (3, 12.706),     # df 1
    (32, 2.042),     # df 30, in the table
    (33, 2.042),     # df 31 falls back to df 30, conservatively
    (59, 2.021),     # df 57 -> df 40
    (62, 2.000),     # df 60
    (121, 2.000),    # df 119 -> df 60
    (122, 1.980),    # df 120
    (500, 1.980),    # df 498 -> df 120
])
def test_drift_uses_the_next_lower_tabled_df(n, t):
    assert _t_used(n) == pytest.approx(t, rel=1e-9)


def test_drift_refuses_fewer_than_three_points():
    refused(stats.E_DRIFT_POINTS, drift, [1, 2], [1, 2])


def test_drift_refuses_mismatched_lengths():
    refused(stats.E_DRIFT_LENGTH, drift, [1, 2, 3], [1, 2])


def test_drift_refuses_all_equal_xs():
    refused(stats.E_DRIFT_FLAT, drift, [5, 5, 5], [1, 2, 3])


# --- Spread between runs --------------------------------------------------------------------

def test_spread_of_three_runs():
    s = spread([10, 12, 14])
    # mean 12; squared deviations 4, 0, 4 -> sample variance 8/2 = 4 -> stdev 2.
    assert s == Spread(n=3, min=10, max=14, mean=12.0, stdev=2.0)


def test_spread_of_one_run_has_no_stdev():
    assert spread([7]) == Spread(n=1, min=7, max=7, mean=7.0, stdev=None)


def test_spread_refuses_no_runs():
    refused(stats.E_SPREAD_EMPTY, spread, [])
