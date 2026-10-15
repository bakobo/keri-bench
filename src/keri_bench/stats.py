"""The statistics every reported number goes through (test plan, Statistics and Soak).

One named percentile definition, one saturation rule and one drift estimator, so two reports can
only differ because the measurements differ. Stdlib only: the definitions are small enough to own,
and owning them keeps the numbers independent of a numerics library's version.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from itertools import accumulate
import statistics
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal, Sequence

from .errors import BenchError

E_HIST_VALUE = "e.input.range.histogram.value.f"
E_HIST_COUNT = "e.input.range.histogram.count.f"
E_HIST_PERCENTILE = "e.input.range.histogram.percentile.f"
E_HIST_EMPTY = "e.state.missing.histogram.samples.f"
E_HIST_MERGE = "e.input.format.histogram.merge.f"
E_LATENCY_LENGTH = "e.input.range.latency.length.f"
E_LATENCY_NEGATIVE = "e.input.range.latency.negative.f"
E_SAT_STEPS = "e.input.range.saturation.steps.f"
E_SAT_ORDER = "e.input.range.saturation.order.f"
E_DRIFT_POINTS = "e.input.range.drift.points.f"
E_DRIFT_LENGTH = "e.input.range.drift.length.f"
E_DRIFT_FLAT = "e.input.range.drift.xs.f"
E_SPREAD_EMPTY = "e.input.range.spread.empty.f"

SUB_BUCKET_BITS = 11
"""2**11 = 2048 is the smallest power of two >= 2 * 10**3, the standard HDR layout for three
significant decimal digits. Values below 2048 are exact; above, bucket b has width 2**b, so the
relative error stays under 1/1024."""

MAX_VALUE = 2**63 - 1
"""Nanoseconds; the signed 64-bit range other HDR implementations share, about 292 years."""


def _is_int(v: object) -> bool:
    # bool is an int subclass, and True as a latency is a caller's bug, not one nanosecond.
    return isinstance(v, int) and not isinstance(v, bool)


class Histogram:
    """An HDR-style histogram of non-negative integer nanoseconds at 3 significant digits.

    Counts are kept per bucket slot, keyed by the slot's lowest value. The exact min and max are
    kept beside them because they cost nothing and a report quoting max should not round it.
    """

    def __init__(self) -> None:
        self._slots: dict[int, int] = {}
        self._count = 0
        self._min: int | None = None
        self._max: int | None = None

    @staticmethod
    def _check(value: object) -> int:
        if not _is_int(value) or not 0 <= value <= MAX_VALUE:
            raise BenchError(E_HIST_VALUE, f"A histogram value must be an integer number of "
                             f"nanoseconds from 0 to 2**63 - 1, and {value!r} is not.")
        return value

    @staticmethod
    def _lowest(value: int) -> int:
        shift = max(0, value.bit_length() - SUB_BUCKET_BITS)
        return value >> shift << shift

    @staticmethod
    def upper_bound(value: int) -> int:
        """The highest value that shares ``value``'s slot, which is what a percentile quotes."""
        value = Histogram._check(value)
        width = 1 << max(0, value.bit_length() - SUB_BUCKET_BITS)
        return Histogram._lowest(value) + width - 1

    def record(self, value: int, count: int = 1) -> None:
        value = self._check(value)
        if not _is_int(count) or count < 1:
            raise BenchError(E_HIST_COUNT, f"A histogram record count must be a positive "
                             f"integer, and {count!r} is not.")
        key = self._lowest(value)
        self._slots[key] = self._slots.get(key, 0) + count
        self._count += count
        self._min = value if self._min is None else min(self._min, value)
        self._max = value if self._max is None else max(self._max, value)

    @property
    def count(self) -> int:
        return self._count

    def _nonempty(self) -> None:
        if not self._count:
            raise BenchError(E_HIST_EMPTY, "The histogram has no samples, so it has no "
                             "percentiles and no extremes.")

    @property
    def min(self) -> int:
        self._nonempty()
        return self._min  # type: ignore[return-value]

    @property
    def max(self) -> int:
        self._nonempty()
        return self._max  # type: ignore[return-value]

    def percentile(self, p: int | float | Fraction) -> int:
        """Nearest rank, rank = ceil(p/100 * count), quoted as its slot's upper bound.

        p is taken through its decimal spelling, so 99.9 means 999/10 exactly; binary float
        arithmetic would turn rank 999 of 1000 into 999.0000000000001 and round it up.
        """
        if (not isinstance(p, (int, float, Fraction)) or isinstance(p, bool)
                or not 0 < p <= 100):
            raise BenchError(E_HIST_PERCENTILE, f"A percentile must be a number greater than 0 "
                             f"and at most 100, and {p!r} is not.")
        self._nonempty()
        rank = math.ceil(Fraction(str(p)) * self._count / 100)
        keys = sorted(self._slots)
        cumulative = list(accumulate(self._slots[key] for key in keys))
        return self.upper_bound(keys[bisect_left(cumulative, rank)])

    def merge(self, other: Histogram) -> None:
        """Add ``other``'s samples to this one; pooling runs is the reason this exists."""
        if not isinstance(other, Histogram):
            raise BenchError(E_HIST_MERGE, f"Only a Histogram can be merged into a Histogram, "
                             f"and {type(other).__name__} is not one.")
        for key, n in other._slots.items():
            self._slots[key] = self._slots.get(key, 0) + n
        if other._count:
            self._count += other._count
            self._min = other._min if self._min is None else min(self._min, other._min)
            self._max = other._max if self._max is None else max(self._max, other._max)


def open_loop_latencies(scheduled: Sequence[int], completed: Sequence[int]) -> list[int]:
    """Latency from the scheduled send time, the coordinated-omission correction.

    Measuring from the scheduled time charges a stall to every request that should have gone out
    during it, rather than only to the one request that was in flight.
    """
    if len(scheduled) != len(completed):
        raise BenchError(E_LATENCY_LENGTH, f"There are {len(scheduled)} scheduled times and "
                         f"{len(completed)} completion times, and they must pair one to one.")
    out = [c - s for s, c in zip(scheduled, completed)]
    for i, v in enumerate(out):
        if v < 0:
            raise BenchError(E_LATENCY_NEGATIVE, f"Request {i} completed {-v} ns before it was "
                             f"scheduled, which no clock in one domain can produce.")
    return out


@dataclass(frozen=True)
class Saturation:
    index: int
    offered: float
    reason: Literal["throughput", "latency"]


def saturation(steps: Sequence[tuple[float, float, float]], epsilon: float = 0.02,
               k: float = 5.0) -> Saturation | None:
    """The first offered-load step past which the system stops keeping up, or None.

    Each step is (offered, achieved, p99_ns). A step saturates when achieved throughput rises by
    less than epsilon over the previous step, or when p99 exceeds k times p99 at the lowest step.
    When both trip at once the reason given is throughput, because that is the primary sign.
    """
    if len(steps) < 2:
        raise BenchError(E_SAT_STEPS, f"Saturation needs at least two offered-load steps to "
                         f"compare, and {len(steps)} were given.")
    for i in range(1, len(steps)):
        if not steps[i][0] > steps[i - 1][0]:
            raise BenchError(E_SAT_ORDER, f"Offered load must strictly increase from step to "
                             f"step, and step {i} offers {steps[i][0]} after "
                             f"{steps[i - 1][0]}.")
    p99_floor = steps[0][2]
    for i in range(1, len(steps)):
        offered, achieved, p99 = steps[i]
        if achieved < steps[i - 1][1] * (1 + epsilon):
            return Saturation(i, offered, "throughput")
        if p99 > k * p99_floor:
            return Saturation(i, offered, "latency")
    return None


T_975: dict[int, float] = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
    10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110,
    18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
    26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042, 40: 2.021, 60: 2.000, 120: 1.980,
}
"""Student's t at 0.975 (two-sided 95%) by degrees of freedom, to three decimals.

A df between entries uses the next lower entry, whose t is larger, so the interval is never
narrower than the true one. That rule also covers df above 120, which gets 1.980 rather than the
normal 1.960: the difference is at most 1%, and it errs wide."""


def _t(df: int) -> float:
    return T_975[max(d for d in T_975 if d <= df)]


@dataclass(frozen=True)
class Drift:
    slope: float
    low: float
    high: float
    n: int


def drift(xs: Sequence[float], ys: Sequence[float]) -> Drift:
    """Ordinary least squares slope of ys on xs, with a 95% confidence interval.

    A soak reports drift as this slope (p50 or p99 per 10**k credentials), so the interval is
    what says whether the drift is real or noise.
    """
    n = len(xs)
    if n != len(ys):
        raise BenchError(E_DRIFT_LENGTH, f"Drift needs one y per x, and there are {n} xs and "
                         f"{len(ys)} ys.")
    if n < 3:
        raise BenchError(E_DRIFT_POINTS, f"A slope's confidence interval needs at least three "
                         f"points, and {n} were given.")
    xbar, ybar = math.fsum(xs) / n, math.fsum(ys) / n
    sxx = math.fsum((x - xbar) ** 2 for x in xs)
    if sxx == 0:
        raise BenchError(E_DRIFT_FLAT, "Every x is the same, so no slope can be fitted.")
    slope = math.fsum((x - xbar) * (y - ybar) for x, y in zip(xs, ys)) / sxx
    sse = math.fsum((y - ybar - slope * (x - xbar)) ** 2 for x, y in zip(xs, ys))
    half = _t(n - 2) * math.sqrt(sse / (n - 2) / sxx)
    return Drift(slope, slope - half, slope + half, n)


@dataclass(frozen=True)
class Spread:
    n: int
    min: float
    max: float
    mean: float
    stdev: float | None


def spread(values: Sequence[float]) -> Spread:
    """The between-run spread a reported point carries beside its pooled distribution.

    The stdev is the sample (n - 1) one, and is None for a single run, which has no spread.
    """
    if not values:
        raise BenchError(E_SPREAD_EMPTY, "Spread needs at least one run's value, and none "
                         "were given.")
    sd = statistics.stdev(values) if len(values) > 1 else None
    return Spread(len(values), min(values), max(values), statistics.fmean(values), sd)
