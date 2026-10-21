"""The recording HSM profile and counting signer behind hsm-sweep (this.i 2p26rn9h)."""

import asyncio
import json

import pytest
from heti.anchored.produce import HsmProfile, SoftwareSigner

from keri_bench.stacks import signer


class Virtual:
    """Virtual time for a profile: sleeping advances the clock instead of waiting."""

    def __init__(self):
        self.now = 0.0

    def clock(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds
        await asyncio.sleep(0)


async def schedule(profile_cls, clock, n, **kw):
    profile = profile_cls(clock=clock.clock, sleep=clock.sleep, **kw)
    finished = []

    async def turn():
        await profile.wait_turn()
        finished.append(round(clock.now, 6))

    await asyncio.gather(*(turn() for _ in range(n)))
    return profile, sorted(finished)


async def test_the_recording_profile_keeps_heti_s_schedule():
    for kw in ({"latency": 0.01, "rate": 20.0}, {"latency": 0.05}, {"rate": 5.0}, {}):
        _, theirs = await schedule(HsmProfile, Virtual(), 6, **kw)
        ours, mine = await schedule(signer.RecordingProfile, Virtual(), 6, **kw)
        assert mine == theirs, kw
        assert len(ours.operations) == 6


async def test_each_turn_is_recorded_with_its_kind_and_its_times():
    clock = Virtual()
    times = iter(range(0, 10_000, 10))
    profile = signer.RecordingProfile(latency=0.01, rate=10.0, clock=clock.clock,
                                      sleep=clock.sleep, ns=lambda: next(times))
    profile.kind = "incept"
    await profile.wait_turn()
    await profile.wait_turn()
    (k1, r1, s1, f1), (k2, r2, s2, f2) = profile.operations
    assert (k1, k2) == ("incept", "sign")  # the kind resets to sign after each turn
    assert r1 <= s1 <= f1 <= r2 <= s2 <= f2


async def test_the_counting_signer_signs_through_the_profile_and_names_each_kind():
    clock = Virtual()
    counted = signer.counting(SoftwareSigner(), latency_ms=5.0, rate=None, clock=clock.clock,
                              sleep=clock.sleep)
    keys = await counted.incept()
    assert (await counted.keys()) == keys
    await counted.sign(data=b"x")
    await counted.rotate(current=keys.verkey)
    assert [op[0] for op in counted.profile.operations] == ["incept", "sign", "rotate"]
    assert clock.now == pytest.approx(0.015)
    report = counted.report()
    assert (report["latency_ms"], report["rate"], len(report["operations"])) == (5.0, None, 3)
    assert signer.counting(SoftwareSigner(), latency_ms=None, rate=4.0).report()[
        "latency_ms"] is None


async def test_the_stats_route_answers_the_signer_s_record_and_nothing_else():
    counted = signer.counting(SoftwareSigner(), latency_ms=None, rate=None)
    await counted.incept()
    status, body = await signer.handler(counted)("GET", "/signer", b"")
    assert status == 200 and json.loads(json.dumps(body))["operations"][0][0] == "incept"
    assert (await signer.handler(counted)("POST", "/signer", b""))[0] == 404


async def test_the_device_keeps_time_even_while_the_caller_s_loop_is_blocked():
    """this.i 0v8kmser: a turn runs on the device's own loop, so a caller that blocks its loop
    for 100 ms delays noticing the answer, not the device's 20 ms."""
    import threading
    import time

    device = signer.Device()
    try:
        counted = signer.counting(SoftwareSigner(), latency_ms=20.0, rate=None, device=device)
        await counted.incept()
        task = asyncio.ensure_future(counted.sign(data=b"x"))
        await asyncio.sleep(0)  # the turn is handed to the device
        time.sleep(0.1)  # the caller's loop is busy, as imbu's is during an issuance
        await task
        kind, requested, started, finished = counted.profile.operations[-1]
        assert kind == "sign"
        assert 19e6 <= finished - started <= 25e6
        assert counted.profile.threads == {device.thread.name}
        assert device.thread.name != threading.current_thread().name
    finally:
        device.close()
    assert not device.thread.is_alive()
