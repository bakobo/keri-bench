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
KNOBS = {"toad": 1, "poll_ms": 1.0, "operation_timeout_s": 2.0, "homes": 1, "clients": 1}


class FakeImbu:
    """202 plus an operation; the operation is pending once, then done or failed."""

    def __init__(self) -> None:
        self.reads: dict[str, int] = {}
        self.outcome = "done"
        self.requests = []
        self.resolves_to = "Eholder"
        self.issued_milestones = {"accepted": 1, "anchored": 3_000,
                                  "witnessed": "1970-01-01T00:00:00.000004+00:00",
                                  "published": 5_500, "finished": 6_000}

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
            if op == "op-meet":
                return 200, {"state": "done", "milestones": {"accepted": 1, "finished": 2},
                             "result": ({"aid": self.resolves_to}
                                        if isinstance(self.resolves_to, str)
                                        else self.resolves_to)}
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
            return 200, {"state": "done", "milestones": self.issued_milestones,
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


def adapter(url, sink, shared=None, *, urls=None, clients=1, run_dir=None):
    urls = urls or [url]
    knobs = {**KNOBS, "homes": len(urls), "clients": clients}
    ctx = AdapterContext(imbu.ImbuIssuer.declaration, run_dir=run_dir, knobs=knobs, peers={},
                         procs=None, sink=sink.append, harness_clock=H, realtime_clock=W,
                         shared=shared)
    issuer = imbu.ImbuIssuer(ctx)
    issuer.homes = [imbu.Home(h, control, [imbu.Client(fiki.Key.generate())
                                           for _ in range(clients)])
                    for h, control in enumerate(urls)]
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
    assert got[1:4] == [("anchored", 3_000, "reported", W, "issuer"),
                        ("witnessed", 4_000, "reported", W, "witness_pool"),
                        ("published", 5_500, "reported", W, "state_distribution")]
    assert [e.name for e in sink[4:]] == ["revocation_requested", "revocation_anchored",
                                          "revocation_witnessed", "revocation_published"]
    assert [e.t for e in sink[5:]] == [5_000, 7_000, 9_000]
    assert sink[1].attrs["said"] == "Esaid"
    assert issuer.ctx.shared["said"] == {"c1": "Esaid"}


async def test_a_failed_operation_or_a_refusal_is_a_coded_error(stand_in):
    sink = []
    issuer = adapter(stand_in.url, sink)
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
            await issuer._finish(issuer.homes[0], issuer.homes[0].clients[0], "op-missing")
        assert gone.value.code == "e.party.imbu.refused.f"
        stand_in.outcome = "done"
        stand_in.resolves_to = "Eother"
        with pytest.raises(BenchError) as stranger:
            await issuer.meet({"aid": "Eholder", "oobi": "http://w/oobi/Eholder"})
        assert stranger.value.code == "e.input.format.imbu.operation.f"
        stand_in.issued_milestones = {**stand_in.issued_milestones, "published": None}
        with pytest.raises(BenchError) as unstamped:
            await issuer.issue("c5", {"aid": "Eholder"})
        assert unstamped.value.code == "e.input.format.imbu.operation.f"
        stand_in.issued_milestones = {"accepted": 1, "anchored": 3_000, "finished": 6_000}
        with pytest.raises(BenchError) as unowed:
            await issuer.issue("c8", {"aid": "Eholder"})
        assert unowed.value.code == "e.input.format.imbu.operation.f"
        stand_in.issued_milestones = {"accepted": 1, "anchored": 3_000, "witnessed": 4_000,
                                      "published": "yesterday", "finished": 6_000}
        with pytest.raises(BenchError):
            await issuer.issue("c9", {"aid": "Eholder"})
        # A refused operation leaves no milestone behind it.
        assert [e.name for e in sink if set(e.credentials) & {"c8", "c9"}] == ["accepted"] * 2
        stand_in.resolves_to = ["Eholder"]
        with pytest.raises(BenchError) as listed:
            await issuer.meet({"aid": "Eholder", "oobi": "http://w/oobi/Eholder"})
        assert listed.value.code == "e.input.format.imbu.operation.f"
        stand_in.issued_milestones = ["anchored"]
        with pytest.raises(BenchError) as shapeless:
            await issuer.issue("c6", {"aid": "Eholder"})
        assert shapeless.value.code == "e.input.format.imbu.operation.f"
        issuer.homes[0].control = stand_in.url + "/elsewhere"
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


@pytest.fixture
async def second():
    fake = FakeImbu()
    server = await http.serve(fake.handle, 0)
    fake.url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    yield fake
    server.close()
    await server.wait_closed()


async def test_credentials_are_dealt_across_homes_then_clients_and_revoked_where_issued(
        stand_in, second):
    sink = []
    issuer = adapter(None, sink, urls=[stand_in.url, second.url], clients=2)
    try:
        await issuer.meet({"aid": "Eholder", "oobi": "http://w/oobi/Eholder"})
        for i in range(5):
            await issuer.issue(f"c{i}", {"aid": "Eholder"})
        await issuer.revoke("c3")
    finally:
        await issuer.stop()
    # Both homes resolved the holder before anything was issued.
    assert ("POST", "/v1/imbu/resolutions") in stand_in.requests
    assert ("POST", "/v1/imbu/resolutions") in second.requests
    accepted = [(e.credentials[0], e.attrs["home"], e.attrs["client"])
                for e in sink if e.name == "accepted"]
    # Home first, then client: c0 h0k0, c1 h1k0, c2 h0k1, c3 h1k1, c4 h0k0.
    assert accepted == [("c0", 0, 0), ("c1", 1, 0), ("c2", 0, 1), ("c3", 1, 1), ("c4", 0, 0)]
    anchored = {e.credentials[0]: e.attrs["home"] for e in sink if e.name == "anchored"}
    assert anchored == {"c0": 0, "c1": 1, "c2": 0, "c3": 1, "c4": 0}
    assert sum(1 for r in stand_in.requests if r == ("POST", "/v1/imbu/issuances")) == 3
    # c3 was issued by the second home, so only it is asked to revoke.
    assert ("POST", "/v1/imbu/revocations") in second.requests
    assert ("POST", "/v1/imbu/revocations") not in stand_in.requests
    revoked = [e for e in sink if e.name == "revocation_anchored"]
    assert revoked[0].attrs == {"home": 1, "client": 1, "adapter": "imbu.issuer"}
    # Each client signs with its own key.
    keys = {c.key.aid for home in issuer.homes for c in home.clients}
    assert len(keys) == 4


@pytest.mark.parametrize("homes, clients", [(0, 1), (1, 0), (-1, 2)])
async def test_a_deployment_with_no_home_or_no_client_is_refused_before_it_starts(homes,
                                                                                  clients):
    ctx = AdapterContext(imbu.ImbuIssuer.declaration, run_dir=None,
                         knobs={**KNOBS, "homes": homes, "clients": clients}, peers={},
                         procs=None, sink=[].append, harness_clock=H, realtime_clock=W)
    with pytest.raises(BenchError) as refused:
        await imbu.ImbuIssuer(ctx).start()
    assert refused.value.code == "e.input.range.imbu.deployment.f"


async def test_the_footprint_is_every_byte_under_every_home(tmp_path):
    issuer = adapter("http://unused", [], run_dir=tmp_path)
    assert await issuer.footprint() == 0
    (tmp_path / "imbu" / "h0" / "db").mkdir(parents=True)
    (tmp_path / "imbu" / "h0" / "db" / "data.mdb").write_bytes(b"x" * 300)
    (tmp_path / "imbu" / "h1").mkdir()
    (tmp_path / "imbu" / "h1" / "ops.sqlite3").write_bytes(b"y" * 45)
    assert await issuer.footprint() == 345


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


async def test_a_file_that_vanishes_while_the_store_is_sized_is_not_counted(tmp_path,
                                                                           monkeypatch):
    issuer = adapter("http://unused", [], run_dir=tmp_path)
    (tmp_path / "imbu" / "h0").mkdir(parents=True)
    (tmp_path / "imbu" / "h0" / "kept").write_bytes(b"x" * 10)
    (tmp_path / "imbu" / "h0" / "ops.sqlite3-journal").write_bytes(b"y" * 99)
    real = imbu.os.path.getsize

    def racing(path):
        if path.endswith("-journal"):
            raise FileNotFoundError(path)  # SQLite removed it between the listing and the stat
        return real(path)

    monkeypatch.setattr(imbu.os.path, "getsize", racing)
    assert await issuer.footprint() == 10
