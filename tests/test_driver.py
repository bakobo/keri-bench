"""The open-loop driver and its self-check (this.i 808tr3fn; Part B #5)."""

import asyncio
import time

import pytest

from keri_bench.driver import OpenLoopDriver, check_driver
from keri_bench.errors import BenchError

MS = 1_000_000
S = 1_000_000_000


async def test_requests_keep_going_out_on_schedule_while_the_system_stalls():
    never = asyncio.Event()
    dispatched = []

    def fire(index, scheduled):
        dispatched.append((index, scheduled, time.monotonic_ns()))
        asyncio.get_running_loop().create_task(never.wait())  # a stalled system: never completes

    driver = OpenLoopDriver(rate=200.0, duration_s=0.5)
    sent = await driver.run(fire)
    assert sent == 100 and len(dispatched) == 100
    assert [i for i, _, _ in dispatched] == list(range(100))
    gaps = {b - a for (_, a, _), (_, b, _) in zip(dispatched, dispatched[1:])}
    assert gaps <= {5 * MS - 1, 5 * MS, 5 * MS + 1}  # rounding of i * period only
    lags = sorted(actual - scheduled for _, scheduled, actual in dispatched)
    assert lags[0] >= 0 and lags[len(lags) // 2] < 5 * MS
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()


async def test_the_driver_can_stop_early():
    driver = OpenLoopDriver(rate=1000.0, duration_s=10.0)
    fired = []

    def fire(index, scheduled):
        fired.append(index)
        if index == 4:
            driver.stop()

    assert await driver.run(fire) == 5


def test_the_driver_refuses_a_nonsense_schedule():
    with pytest.raises(BenchError):
        OpenLoopDriver(rate=0.0, duration_s=1.0)
    with pytest.raises(BenchError):
        OpenLoopDriver(rate=1.0, duration_s=0.0)


def schedule(n, period=10 * MS):
    return [i * period for i in range(n)]


@pytest.mark.parametrize("lag, ok", [(5 * MS - 50_000, True), (5 * MS + 50_000, False)])
def test_a_run_is_refused_just_above_the_lag_limit_and_accepted_just_below(lag, ok):
    scheduled = schedule(100)
    dispatched = [t + lag for t in scheduled]
    verdict = check_driver(scheduled, dispatched, lag_limit_ns=5 * MS)
    assert verdict.ok is ok
    assert (verdict.reason is None) is ok
    # The lag is quoted as its histogram bucket's upper bound, never below the true value.
    assert verdict.p99_lag_ns >= lag


def test_a_growing_backlog_over_three_windows_is_refused_even_within_the_lag_limit():
    # Every request leaves, each later than the last in proportion to its schedule: a driver
    # falling further behind each second, under a lag limit set high enough not to fire first.
    scheduled = schedule(300, period=10 * MS)
    dispatched = [s + s // 2 for s in scheduled]
    verdict = check_driver(scheduled, dispatched, lag_limit_ns=10 * S)
    assert not verdict.ok
    assert "backlog" in verdict.reason
    assert verdict.backlog == sorted(verdict.backlog) and verdict.backlog[-1] > verdict.backlog[0]


def test_a_steady_backlog_is_not_growth():
    scheduled = schedule(400, period=10 * MS)
    dispatched = [s + 2 * MS for s in scheduled]
    verdict = check_driver(scheduled, dispatched, lag_limit_ns=5 * MS)
    assert verdict.ok and verdict.reason is None


def test_a_request_that_never_left_refuses_the_run():
    verdict = check_driver([0], [None], lag_limit_ns=5 * MS)
    assert not verdict.ok and "never left" in verdict.reason


def test_check_driver_refuses_mismatched_inputs_and_an_empty_run():
    with pytest.raises(BenchError):
        check_driver([1, 2], [1], lag_limit_ns=MS)
    with pytest.raises(BenchError):
        check_driver([], [], lag_limit_ns=MS)
