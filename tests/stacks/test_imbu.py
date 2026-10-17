"""The imbu issuer adapter against a stand-in for imbu's asynchronous operations, shaped as imbu
963dfec answers (src/imbu/operations.py); test_imbu_async runs it against the real imbu."""

import base64
import json

import fiki
import pytest

from keri_bench.adapters.base import AdapterContext
from keri_bench.errors import BenchError
from keri_bench.http import Answer
from keri_bench.stacks import imbu
from keri_bench.synthetic import http

H, W = "monotonic@b", "realtime@h"
KNOBS = {"toad": 1, "poll_ms": 1.0, "operation_timeout_s": 2.0}


class FakeImbu:
    """202 plus an operation; the operation is pending once, then done or failed."""

    def __init__(self) -> None:
        self.reads: dict[str, int] = {}
        self.outcome = "done"
        self.requests = []

    async def handle(self, method, path, body):
        self.requests.append((method, path))
        if method == "POST" and path == "/v1/imbu/issuances":
            request = json.loads(body)
            return 202, {"id": f"op-{request['attributes']['benchCredential']}",
                         "state": "pending", "milestones": {"accepted": 1, "anchored": None}}
        if method == "POST" and path == "/v1/imbu/resolutions":
            return 202, {"id": "op-meet"}
        if method == "POST" and path == "/v1/imbu/revocations":
            return 202, {"operation": {"id": "op-revoke"}}
        if method == "GET" and path.startswith("/v1/imbu/operations/"):
            op = path.rsplit("/", 1)[1]
            self.reads[op] = self.reads.get(op, 0) + 1
            if op == "op-missing":
                return 404, {"error": "no such operation"}
            if self.reads[op] == 1 or self.outcome == "pending":
                return 200, {"state": "pending", "milestones": {"accepted": 1, "anchored": None}}
            if self.outcome == "failed":
                return 200, {"state": "failed", "error": {"code": "e.state.pending.witness.r"}}
            if op == "op-revoke":
                return 200, {"state": "done", "milestones": {
                    "anchored": 5_000, "witnessed": "1970-01-01T00:00:00.000007+00:00",
                    "published": 9_000}}
            return 200, {"state": "done", "milestones": {
                "accepted": 1, "anchored": 3_000,
                "witnessed": "1970-01-01T00:00:00.000004+00:00", "published": None,
                "finished": 6_000},
                "result": {"said": "Esaid", "grant": base64.b64encode(b"grant").decode()}}
        return 404, {}


@pytest.fixture
async def stand_in():
    fake = FakeImbu()
    server = await http.serve(fake.handle, 0)
    fake.url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    yield fake
    server.close()
    await server.wait_closed()


def adapter(url, sink, shared=None):
    ctx = AdapterContext(imbu.ImbuIssuer.declaration, run_dir=None, knobs=KNOBS, peers={},
                         procs=None, sink=sink.append, harness_clock=H, realtime_clock=W,
                         shared=shared)
    issuer = imbu.ImbuIssuer(ctx)
    issuer.key, issuer.control = fiki.Key.generate(), url
    return issuer


async def test_issue_and_revoke_report_imbus_own_times(stand_in):
    sink = []
    issuer = adapter(stand_in.url, sink)
    try:
        await issuer.meet({"aid": "Eholder", "oobi": "http://w/oobi/Eholder"})
        issued = await issuer.issue("c1", {"aid": "Eholder"})
        await issuer.revoke("c1")
    finally:
        await issuer.stop()
    assert (issued.said, issued.grant, issued.delivered_by_stack) == ("Esaid", b"grant", False)
    got = [(e.name, e.t, e.method, e.clock, e.role) for e in sink]
    assert got[0][:3] == ("accepted", got[0][1], "observed")
    # A milestone owed and null is not reported: published here, which imbu had not reached.
    assert got[1:3] == [("anchored", 3_000, "reported", W, "issuer"),
                        ("witnessed", 4_000, "reported", W, "witness_pool")]
    assert [e.name for e in sink[3:]] == ["revocation_requested", "revocation_anchored",
                                          "revocation_witnessed", "revocation_published"]
    assert [e.t for e in sink[4:]] == [5_000, 7_000, 9_000]
    assert sink[1].attrs["said"] == "Esaid"
    assert issuer.ctx.shared["said"] == {"c1": "Esaid"}


async def test_a_failed_operation_or_a_refusal_is_a_coded_error(stand_in):
    issuer = adapter(stand_in.url, [])
    try:
        stand_in.outcome = "failed"
        with pytest.raises(BenchError) as failed:
            await issuer.issue("c2", {"aid": "Eholder"})
        assert failed.value.code == "e.party.imbu.operation.f"
        stand_in.outcome = "pending"
        with pytest.raises(BenchError) as late:
            await issuer.issue("c3", {"aid": "Eholder"})
        assert late.value.code == "e.env.imbu.operation.r"
        with pytest.raises(BenchError) as gone:
            await issuer._finish("op-missing")
        assert gone.value.code == "e.party.imbu.refused.f"
        issuer.control = stand_in.url + "/elsewhere"
        with pytest.raises(BenchError) as unmet:
            await issuer.meet({"aid": "Eholder", "oobi": "http://w/oobi/Eholder"})
        assert unmet.value.code == "e.party.imbu.refused.f"
        with pytest.raises(BenchError) as refused:
            await issuer.issue("c4", {"aid": "Eholder"})
        assert refused.value.code == "e.party.imbu.refused.f"
        issuer.ctx.shared["said"] = {"c4": "E4"}
        with pytest.raises(BenchError):
            await issuer.revoke("c4")
    finally:
        await issuer.stop()


def answer(body, headers=None):
    return Answer(202, json.dumps(body).encode() if body is not None else b"x", headers or {})


def test_the_operation_is_named_by_body_or_location():
    assert imbu.operation_id(answer({"operation": "a"})) == "a"
    assert imbu.operation_id(answer({"id": "b"})) == "b"
    assert imbu.operation_id(answer({"operation": {"id": "c"}})) == "c"
    assert imbu.operation_id(answer(None, {"Location": "/v1/imbu/operations/d/"})) == "d"
    with pytest.raises(BenchError) as unnamed:
        imbu.operation_id(answer([1]))
    assert unnamed.value.code == "e.input.format.imbu.operation.f"


@pytest.mark.parametrize("value", ["yesterday", "2026-10-15T00:00:00", 1.5, True, None])
def test_a_stamp_imbu_could_mean_two_ways_is_refused(value):
    with pytest.raises(BenchError):
        imbu.stamp(value)


def test_stamps_are_nanoseconds():
    assert imbu.stamp(12) == 12
    assert imbu.stamp("1970-01-01T00:00:01.5+00:00") == 1_500_000_000
    assert imbu.stamp("1970-01-02T00:00:00Z") == 86_400 * 1_000_000_000
