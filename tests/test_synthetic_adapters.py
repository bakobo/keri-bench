"""The calibration stack's adapters, beyond what a whole run exercises."""

import pytest

from keri_bench.adapters.base import AdapterContext
from keri_bench.errors import BenchError
from keri_bench.procs import ProcessController
from keri_bench.proxy import Exchange
from keri_bench.synthetic import adapters as synthetic
from keri_bench.vocabulary import Role


def test_delays_parse_and_refuse_malformed_items():
    assert synthetic.parse_delays(" a>b=10/0.2, ,c>d=5/0 ") == {"a>b": [10.0, 0.2],
                                                                "c>d": [5.0, 0.0]}
    for bad in ("a>b=10", "ab=10/0.2", "a>b>c=1/1", "a>b=x/0.1"):
        with pytest.raises(BenchError) as refused:
            synthetic.parse_delays(bad)
        assert refused.value.code == "e.input.format.scenario.knob.delays.f"


def context(cls, sink, tmp_path, knobs=None, peers=None, procs=None):
    return AdapterContext(cls.declaration, run_dir=tmp_path, knobs=knobs or {},
                          peers=peers or {}, procs=procs, sink=sink.append,
                          harness_clock="monotonic@b", realtime_clock="realtime@h",
                          observe=lambda link, cb: None)


async def test_an_adapter_that_never_started_stops_cleanly(tmp_path):
    for cls in (synthetic.SyntheticHolder, synthetic.SyntheticWitnessPool):
        await cls(context(cls, [], tmp_path)).stop()


def test_the_state_adapter_ignores_exchanges_that_are_not_publications(tmp_path):
    sink = []
    adapter = synthetic.SyntheticStateDistribution(
        context(synthetic.SyntheticStateDistribution, sink, tmp_path))

    def seen(method="POST", path="/publish/c1", status=200, duplicate=False):
        adapter._seen(Exchange("state_distribution.api", method, path, status, 1, 2, 3,
                               duplicate=duplicate))

    seen(method="GET")
    seen(status=502)
    seen(duplicate=True)
    seen(path="/status/c1")
    assert sink == []
    seen()
    seen(path="/revoke/c1")
    assert [(e.name, e.t) for e in sink] == [("published", 3), ("revocation_published", 3)]


async def test_the_verifier_runs_a_cold_sync_and_refuses_an_error_answer(tmp_path):
    procs = ProcessController(tmp_path)
    knobs = {"seed": 1, "clock_skew_ms": 0.0,
             "delays": "sync_full_started>sync_full_done=2/0.1"}
    sink, peers = [], {}
    state = synthetic.SyntheticStateDistribution(
        context(synthetic.SyntheticStateDistribution, [], tmp_path, knobs, peers, procs))
    peers[Role.STATE_DISTRIBUTION] = await state.start()
    verifier = synthetic.SyntheticVerifier(
        context(synthetic.SyntheticVerifier, sink, tmp_path, knobs, peers, procs))
    peers[Role.VERIFIER] = await verifier.start()
    try:
        await verifier.sync_cold()
        assert [e.name for e in sink] == ["sync_full_started", "sync_full_done"]
        assert await verifier.identity() == {"aid": "Vsynthetic"}
        peers[Role.VERIFIER] = {"api": peers[Role.VERIFIER]["api"] + "/nowhere"}
        with pytest.raises(BenchError) as refused:
            await verifier.status("c1")
        assert refused.value.code == "e.state.pending.synthetic.f"
    finally:
        await verifier.stop()
        await state.stop()
        await procs.stop_all()
