"""The open-loop driver, and the check that refuses a run whose driver was the bottleneck.

Requests go out on a schedule fixed before the run, never waiting for a completion, because a
closed loop hides exactly the queueing a saturated issuance lock produces (coordinated omission).
Latency is measured from the scheduled time (the ``requested`` milestone), and a run whose driver
fell behind its own schedule is refused rather than reported (this.i 808tr3fn; test plan,
Statistics: driver lag).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .errors import BenchError
from .stats import Histogram

E_SCHEDULE = "e.input.range.driver.schedule.f"
E_CHECK = "e.input.range.driver.check.f"

WINDOW_NS = 1_000_000_000

GROWTH_WINDOWS = 3


class OpenLoopDriver:
    # One event loop drives every lifecycle; on kelun that tops out near 60 full lifecycles per
    # second, beyond which the self-check refuses the run (~2eud).
    def __init__(self, rate: float, duration_s: float, *,
                 clock: Callable[[], int] = time.monotonic_ns) -> None:
        if not rate > 0 or not duration_s > 0:
            raise BenchError(E_SCHEDULE, f"An open-loop schedule needs a positive rate and "
                             f"duration, not {rate!r} per second for {duration_s!r} s.")
        self.rate, self.duration_s, self.clock = rate, duration_s, clock
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    async def run(self, fire: Callable[[int, int], None]) -> int:
        """Call ``fire(index, scheduled_ns)`` at each scheduled time; ``fire`` must not block.

        Returns how many requests went out.
        """
        count = int(self.rate * self.duration_s)
        period = 1e9 / self.rate
        start = self.clock()
        sent = 0
        for index in range(count):
            if self._stopped:
                break
            scheduled = start + round(index * period)
            # Yield rather than sleep: a thread blocked in epoll is woken late by the OS, and
            # asyncio rounds its timeouts to whole milliseconds (this.i, the driver holds a
            # core). Every other task on the loop still runs between yields.
            while self.clock() < scheduled:
                await asyncio.sleep(0)
            fire(index, scheduled)
            sent += 1
        return sent


@dataclass(frozen=True)
class DriverVerdict:
    ok: bool
    p99_lag_ns: int
    backlog: list[int]
    """Requests scheduled but not yet dispatched at the end of each one-second window."""
    reason: str | None


def check_driver(scheduled: Sequence[int], dispatched: Sequence[int | None], *,
                 lag_limit_ns: int) -> DriverVerdict:
    """Refuse when p99 send lag exceeds the limit, or when the backlog grows across three
    consecutive one-second windows. ``None`` in ``dispatched`` is a request that never left."""
    if len(scheduled) != len(dispatched) or not scheduled:
        raise BenchError(E_CHECK, f"The driver check needs one dispatch time (or None) per "
                         f"scheduled request, and at least one request; it got "
                         f"{len(scheduled)} and {len(dispatched)}.")
    lags = Histogram()
    for planned, actual in zip(scheduled, dispatched):
        if actual is not None:
            lags.record(max(0, actual - planned))
    p99 = lags.percentile(99) if lags.count else 0
    first, last = min(scheduled), max(scheduled)
    windows = (last - first) // WINDOW_NS + 1
    backlog = []
    for window in range(windows):
        end = first + (window + 1) * WINDOW_NS
        due = sum(1 for t in scheduled if t < end)
        gone = sum(1 for t in dispatched if t is not None and t < end)
        backlog.append(due - gone)
    rises, longest = 0, 0
    for before, after in zip([0] + backlog, backlog):
        rises = rises + 1 if after > before else 0
        longest = max(longest, rises)
    reason = None
    if p99 > lag_limit_ns:
        reason = (f"The driver's p99 send lag was {p99} ns, above the {lag_limit_ns} ns limit, "
                  f"so the driver, not the system, set the pace.")
    elif longest >= GROWTH_WINDOWS:
        reason = (f"The driver's backlog grew across {longest} consecutive one-second windows "
                  f"({backlog}), so it was falling behind its schedule.")
    return DriverVerdict(reason is None, p99, backlog, reason)
