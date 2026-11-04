"""Stage timing for one heti verification (this.i 3jeks5st), against stand-ins for heti's
functions; the timed verifier runs for real in test_stack.py."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from keri_bench.stacks import stages


class Clock:
    """A monotonic clock that a test moves by hand."""

    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    tick = Clock()
    monkeypatch.setattr(stages, "_now", tick)
    return tick


def test_an_attempt_charges_each_stage_and_counts_its_calls(clock):
    attempt = stages.Attempt("c1", holder="Eholder")
    with attempt.active():
        with stages.span("tel.vet"):
            clock.now += 7
        with stages.span("tel.vet"):
            clock.now += 3
    assert attempt.stages["tel.vet"] == 10 and attempt.calls["tel.vet"] == 2
    assert attempt.stages["signatures"] == 0 and attempt.calls["signatures"] == 0


def test_the_outermost_span_wins_so_nothing_is_counted_twice(clock):
    attempt = stages.Attempt("c1", holder="Eholder")
    with attempt.active():
        with stages.span("meet"):
            clock.now += 5
            with stages.span("key_state.issuer"):
                clock.now += 4
    assert attempt.stages["meet"] == 9 and attempt.stages["key_state.issuer"] == 0
    assert attempt.calls["key_state.issuer"] == 0


def test_a_span_outside_any_attempt_times_nothing(clock):
    with stages.span("tel.vet"):
        clock.now += 1  # nothing to charge, and no error


def test_a_span_ends_even_when_the_stage_raises(clock):
    attempt = stages.Attempt("c1", holder="Eholder")
    with attempt.active():
        with pytest.raises(ValueError):
            with stages.span("tel.vet"):
                clock.now += 2
                raise ValueError("refused")
        with stages.span("signatures"):
            clock.now += 1
    assert attempt.stages == {**dict.fromkeys(stages.STAGES, 0), "tel.vet": 2, "signatures": 1}


def test_an_unknown_stage_is_a_bug_in_the_bench():
    with pytest.raises(stages.BenchError) as wrong:
        with stages.span("nowhere"):
            pass
    assert wrong.value.code == stages.E_STAGE


def test_key_state_is_labelled_by_whose_it_is(clock):
    attempt = stages.Attempt("c1", holder="Eholder")
    assert attempt.key_state("Eholder") == "key_state.holder"
    assert attempt.key_state("Eissuer") == "key_state.issuer"


def test_attempts_that_ran_at_the_same_time_are_marked_overlapped(clock):
    first, second, third = (stages.Attempt(c, holder="E") for c in ("c1", "c2", "c3"))
    with first.active():
        with second.active():
            pass
    with third.active():
        pass
    assert first.overlapped and second.overlapped and not third.overlapped


async def test_concurrent_tasks_keep_their_own_attempts(clock):
    async def one(credential, hold):
        attempt = stages.Attempt(credential, holder="E")
        with attempt.active():
            with stages.span("tel.fetch"):
                clock.now += 1
                await asyncio.sleep(hold)
        return attempt

    a, b = await asyncio.gather(one("c1", 0.01), one("c2", 0))
    assert a.calls["tel.fetch"] == b.calls["tel.fetch"] == 1
    assert a.overlapped and b.overlapped


def test_a_record_is_one_json_line_with_the_total_and_the_outcome(clock):
    attempt = stages.Attempt("c1", holder="Eholder")
    with attempt.active():
        with attempt.phase("admit"):
            with stages.span("signatures"):
                clock.now += 3
            clock.now += 2
        with attempt.phase("current"):
            clock.now += 4
    record = attempt.record(outcome="accepted", code=None)
    assert record == {"credential": "c1", "holder": "Eholder", "outcome": "accepted",
                      "code": None,
                      "total_ns": 9, "overlapped": False,
                      "phases": {"admit": 5, "current": 4},
                      "stages": {**dict.fromkeys(stages.STAGES, 0), "signatures": 3},
                      "calls": {**dict.fromkeys(stages.STAGES, 0), "signatures": 1}}
    json.dumps(record)


def test_installing_wraps_each_named_function_and_charges_its_stage(clock):
    calls = []

    def satisfied_by(state, raw, sigs):
        clock.now += 11
        calls.append("sig")
        return ["k0"]

    def vetted(*a, **k):
        clock.now += 5
        return "record"

    def whole(*a, **k):
        clock.now += 1

    async def observe(lockbox, aid, recency, timeout):
        clock.now += 20
        return aid

    async def admit_exn(facet, *, message, grant):
        clock.now += 2
        return b"admit"

    async def add_other(self, *, oobi, alias=None):
        clock.now += 30

    ipex = SimpleNamespace(satisfied_by=satisfied_by, _admit_exn=admit_exn)
    verifying = SimpleNamespace(_observe=observe, _vetted=vetted, _demand_whole=whole)
    Lockbox = type("Lockbox", (), {"add_other": add_other})
    stages.install(ipex=ipex, verifying=verifying, lockbox=Lockbox)

    attempt = stages.Attempt("c1", holder="Eholder")

    async def verification():
        with attempt.active():
            assert ipex.satisfied_by(None, b"", []) == ["k0"]
            assert verifying._vetted() == "record"
            verifying._demand_whole()
            assert await verifying._observe(None, "Eholder", 60, None) == "Eholder"
            await verifying._observe(None, "Eissuer", 60, None)
            assert await ipex._admit_exn(None, message="", grant=None) == b"admit"
            await Lockbox().add_other(oobi="o")

    asyncio.run(verification())
    assert calls == ["sig"]
    assert attempt.stages == {"meet": 30, "key_state.holder": 20, "key_state.issuer": 20,
                              "signatures": 11, "tel.fetch": 0, "tel.vet": 6, "receipt": 2}


def test_installing_refuses_when_heti_no_longer_has_a_name_it_wraps():
    with pytest.raises(stages.BenchError) as missing:
        stages.install(ipex=SimpleNamespace(), verifying=SimpleNamespace(),
                       lockbox=type("Lockbox", (), {}))
    assert missing.value.code == stages.E_MISSING
    assert "satisfied_by" in missing.value.message


def test_installing_against_the_pinned_heti_finds_every_name(monkeypatch):
    from heti.anchored.produce import ipex, lockbox, verifying
    for module, name in ((ipex, "satisfied_by"), (ipex, "_admit_exn"),
                         (verifying, "_observe"), (verifying, "_vetted"),
                         (verifying, "_demand_whole"), (lockbox.Lockbox, "add_other")):
        monkeypatch.setattr(module, name, getattr(module, name))  # restored after the test
    stages.install()
    assert ipex.satisfied_by.__wrapped__ is not None


async def test_a_timed_source_charges_the_observer_read(clock):
    class Source:
        url = "http://observer"

        async def read(self, regid):
            clock.now += 8
            return regid

    source = stages.TimedSource(Source())
    attempt = stages.Attempt("c1", holder="E")
    with attempt.active():
        assert await source.read("Er") == "Er"
    assert attempt.stages["tel.fetch"] == 8 and source.url == "http://observer"


async def test_two_stages_one_attempt_runs_at_once_are_both_charged(clock):
    """Outermost wins within a task; a sibling task under the same attempt is charged too."""
    attempt = stages.Attempt("c1", holder="E")

    async def stage(name, hold):
        with stages.span(name):
            clock.now += 1
            await asyncio.sleep(hold)

    with attempt.active():
        await asyncio.gather(stage("key_state.issuer", 0.01), stage("tel.fetch", 0))
    assert attempt.calls["key_state.issuer"] == attempt.calls["tel.fetch"] == 1
    # Two stages at once are each charged their wall time, so the attempt is marked overlapped
    # and the summary leaves it out rather than report stages summing past the total.
    assert attempt.overlapped


async def test_a_child_task_outliving_its_parents_span_is_still_charged(clock):
    attempt = stages.Attempt("c1", holder="E")
    started = asyncio.Event()

    async def child():
        await started.wait()
        with stages.span("tel.fetch"):
            clock.now += 3

    with attempt.active():
        with stages.span("key_state.issuer"):
            task = asyncio.create_task(child())
            clock.now += 1
        started.set()
        await task
    assert attempt.calls["tel.fetch"] == 1 and attempt.stages["tel.fetch"] == 3
    assert not attempt.overlapped


def test_a_stage_one_attempt_runs_alone_leaves_it_not_overlapped(clock):
    attempt = stages.Attempt("c1", holder="E")
    with attempt.active():
        with stages.span("tel.vet"):
            pass
        with stages.span("tel.vet"):
            pass
    assert not attempt.overlapped


async def test_a_child_task_entering_a_stage_inside_its_parents_span_is_charged_and_overlaps(clock):
    attempt = stages.Attempt("c1", holder="E")
    entered, release = asyncio.Event(), asyncio.Event()

    async def child():
        with stages.span("tel.fetch"):
            entered.set()
            await release.wait()
            clock.now += 3

    with attempt.active():
        with stages.span("key_state.issuer"):
            task = asyncio.create_task(child())
            await entered.wait()
            clock.now += 1
        release.set()
        await task
    # Wall time: the child's span was open while the parent's advanced the clock too.
    assert attempt.calls["tel.fetch"] == 1 and attempt.stages["tel.fetch"] == 4
    assert attempt.overlapped


async def test_a_task_outliving_its_attempt_does_not_change_it(clock):
    attempt = stages.Attempt("c1", holder="E")
    go = asyncio.Event()

    async def late():
        await go.wait()
        with stages.span("tel.fetch"):
            clock.now += 5

    with attempt.active():
        task = asyncio.create_task(late())
    go.set()
    await task
    assert attempt.calls["tel.fetch"] == 0 and attempt.stages["tel.fetch"] == 0


async def test_a_stage_running_past_its_attempts_end_marks_it_overlapped_and_is_not_added(clock):
    attempt = stages.Attempt("c1", holder="E")
    entered, release = asyncio.Event(), asyncio.Event()

    async def child():
        with stages.span("tel.fetch"):
            entered.set()
            await release.wait()
            clock.now += 50

    with attempt.active():
        task = asyncio.create_task(child())
        await entered.wait()
    release.set()
    await task
    assert attempt.overlapped and attempt.calls["tel.fetch"] == 0


def test_an_attempt_nested_inside_anothers_span_is_charged_for_its_own_stages(clock):
    outer, inner = stages.Attempt("c1", holder="E"), stages.Attempt("c2", holder="E")
    with outer.active():
        with stages.span("tel.vet"):
            with inner.active():
                with stages.span("tel.fetch"):
                    clock.now += 2
    assert inner.calls["tel.fetch"] == 1 and inner.stages["tel.fetch"] == 2


def test_two_threads_are_not_one_task(clock):
    import threading
    attempt = stages.Attempt("c1", holder="E")
    done = threading.Event()
    with attempt.active():
        with stages.span("tel.vet"):
            import contextvars
            context = contextvars.copy_context()
            worker = threading.Thread(target=lambda: context.run(_fetch, done))
            worker.start()
            worker.join()
    assert done.is_set() and attempt.calls["tel.fetch"] == 1 and attempt.overlapped


def _fetch(done):
    with stages.span("tel.fetch"):
        done.set()
