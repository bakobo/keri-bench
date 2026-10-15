"""The per-role adapter interface (this.i 2vu4969q, 0ys878gy)."""

import pytest

from keri_bench.adapters.base import (AdapterContext, Declaration, Issued, Observation)
from keri_bench.errors import BenchError
from keri_bench.knobs import Knob, KnobSchema
from keri_bench.vocabulary import Role

H = "monotonic@boot"
W = "realtime@host"

DECL = Declaration(
    adapter="test-issuer", role=Role.ISSUER, version="1",
    milestones={
        "requested": Observation("observed", "harness"),
        "anchored": Observation("reported", "stack:issuer-wall", resolution_ns=1_000),
        "witnessed": Observation("reported", "stack:issuer-wall", resolution_ns=1_000),
        "published": Observation("polled", "harness", resolution_ns=50_000_000),
        "grant_sent": Observation("reported", "realtime", resolution_ns=1_000),
    },
    knobs=KnobSchema(1, {"toad": Knob(int, "receipts")}),
    tested_against={"imbu": "abc"},
)


def context(sink, clock=lambda: 42):
    return AdapterContext(DECL, run_dir=None, knobs={"toad": 2}, peers={}, procs=None,
                          sink=sink.append, clock=clock, harness_clock=H, realtime_clock=W)


def test_emit_fills_method_clock_resolution_and_owner_role_from_the_declaration():
    sink = []
    ctx = context(sink)
    ctx.emit("requested", credentials=["c1"])
    ctx.emit("anchored", credentials=["c1"], t=7_000)
    ctx.emit("witnessed", credentials=["c1", "c2"], t=9_000, batch_id="b", attrs={"k": 1})
    ctx.emit("published", credentials=["c1"])
    ctx.emit("grant_sent", credentials=["c1"], t=5)
    first, anchored, witnessed, published, grant = sink
    assert (first.name, first.t, first.method, first.clock, first.resolution, first.role) == \
        ("requested", 42, "observed", H, 1, "issuer")
    assert (anchored.clock, anchored.resolution, anchored.method) == \
        ("stack:issuer-wall", 1_000, "reported")
    # Filed under the owning role, naming the adapter that reported it (this.i 0ys878gy).
    assert witnessed.role == "witness_pool"
    assert witnessed.attrs == {"k": 1, "adapter": "test-issuer"}
    assert witnessed.credentials == ("c1", "c2") and witnessed.batch_id == "b"
    assert (published.resolution, published.method) == (50_000_000, "polled")
    assert grant.clock == W


def test_emit_refuses_an_undeclared_milestone():
    with pytest.raises(BenchError) as refused:
        context([]).emit("verified", credentials=["c1"])
    assert refused.value.code == "e.rule.adapter.milestone.undeclared.f"


def test_emit_refuses_a_reported_milestone_without_its_stack_time():
    with pytest.raises(BenchError) as refused:
        context([]).emit("anchored", credentials=["c1"])
    assert refused.value.code == "e.rule.adapter.milestone.time.f"


def test_emit_refuses_a_milestone_with_no_credentials():
    with pytest.raises(BenchError) as refused:
        context([]).emit("requested", credentials=[])
    assert refused.value.code == "e.rule.adapter.milestone.credentials.f"


def test_the_context_offers_the_harness_clock_and_resolved_knobs():
    ctx = context([], clock=lambda: 99)
    assert ctx.now() == 99
    assert ctx.knobs == {"toad": 2}
    assert ctx.domain("stack:issuer-wall") == "stack:issuer-wall"
    assert ctx.shared == {}


@pytest.mark.parametrize("bad, code", [
    ({"milestones": {"teleported": Observation("observed", "harness")}},
     "e.input.range.declaration.milestone.f"),
    ({"milestones": {"requested": Observation("guessed", "harness")}},
     "e.input.range.declaration.method.f"),
    ({"milestones": {"requested": Observation("observed", "sundial")}},
     "e.input.range.declaration.clock.f"),
    ({"milestones": {"requested": Observation("polled", "harness")}},
     "e.input.range.declaration.resolution.f"),
    ({"milestones": {"requested": Observation("reported", "harness")}},
     "e.input.range.declaration.clock.f"),
    ({"milestones": {"requested": Observation("observed", "stack:x")}},
     "e.input.range.declaration.clock.f"),
    ({"milestones": {"requested": Observation("observed", "realtime")}},
     "e.input.range.declaration.clock.f"),
    ({"milestones": {"requested": Observation("reported", "stack:x")}},
     "e.input.range.declaration.resolution.f"),
    ({"milestones": {"requested": Observation("reported", "stack:", resolution_ns=1)}},
     "e.input.range.declaration.clock.f"),
    ({"adapter": ""}, "e.input.missing.declaration.adapter.f"),
])
def test_a_malformed_declaration_is_refused(bad, code):
    fields = dict(adapter="a", role=Role.ISSUER, version="1", milestones={},
                  knobs=KnobSchema(1, {}), tested_against={})
    fields.update(bad)
    with pytest.raises(BenchError) as refused:
        Declaration(**fields)
    assert refused.value.code == code


def test_a_declaration_lists_its_stack_clock_domains_and_describes_itself():
    assert DECL.stack_clocks() == {"stack:issuer-wall"}
    described = DECL.describe()
    assert described["adapter"] == "test-issuer" and described["role"] == "issuer"
    assert described["milestones"]["published"] == {
        "method": "polled", "clock": "harness", "resolution_ns": 50_000_000}
    assert described["knobs"]["version"] == 1
    assert described["tested_against"] == {"imbu": "abc"}


def test_issued_carries_what_the_relay_needs():
    issued = Issued(credential="c1", said="E1", grant=b"g", delivered_by_stack=False)
    assert issued.grant == b"g"


async def test_the_base_lifecycle_does_nothing_and_role_operations_must_be_written():
    from keri_bench.adapters import base
    ctx = context([])
    plain = base.Adapter(ctx)
    assert await plain.start() == {} and await plain.connect() == {}
    assert await plain.stop() is None and plain.clock_sources() == {}
    assert await plain.versions() == {}
    assert await base.WitnessPoolAdapter(ctx).anchors("c1") is None
    calls = [base.IssuerAdapter(ctx).issue("c1", {}), base.IssuerAdapter(ctx).revoke("c1"),
             base.HolderAdapter(ctx).identity(), base.HolderAdapter(ctx).receive(None),
             base.HolderAdapter(ctx).present("c1", {}), base.VerifierAdapter(ctx).identity(),
             base.VerifierAdapter(ctx).verify("c1", None), base.VerifierAdapter(ctx).status("c1"),
             base.VerifierAdapter(ctx).sync_cold()]
    for call in calls:
        with pytest.raises(NotImplementedError):
            await call
    assert set(base.ROLE_BASES) == set(Role)
