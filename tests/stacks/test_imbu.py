"""The imbu issuer adapter against a stand-in for imbu's asynchronous operations, shaped as imbu
8512c1d answers (src/imbu/operations.py); test_imbu_async runs it against the real imbu."""

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
KNOBS = {"toad": 1, "poll_ms": 1.0, "wait_s": "n/a", "operation_timeout_s": 2.0, "homes": 1,
         "clients": 1, "signer": "lockbox", "signer_latency_ms": "n/a", "signer_rate": "n/a",
         "answer_rate": 2.0, "answer_burst": 20}


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
    server = await http.serve(fake.handle, 0, headers={"Signature": "sig1=:x:"})
    fake.url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    yield fake
    server.close()
    await server.wait_closed()


def adapter(url, sink, shared=None, *, urls=None, clients=1, run_dir=None, knobs=None):
    urls = urls or [url]
    knobs = {**KNOBS, "homes": len(urls), "clients": clients, **(knobs or {})}
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
    # accepted is imbu's own stamp, as every other milestone is (this.i 5k8z0pxe).
    assert got[0] == ("accepted", 1, "reported", W, "issuer")
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
        # A refused operation leaves no milestone behind it, not even accepted.
        assert [e.name for e in sink if set(e.credentials) & {"c8", "c9"}] == []
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


async def test_the_adapter_counts_the_answers_imbu_signed_and_the_issuances_it_accepted(
        stand_in):
    issuer = adapter(stand_in.url, [])
    try:
        await issuer.meet({"aid": "Eholder", "oobi": "http://w/oobi/Eholder"})
        await issuer.issue("c1", {"aid": "Eholder"})
    finally:
        await issuer.stop()
    home = issuer.homes[0]
    # resolution POST, its poll, the issuance POST and its two polls: five signed answers.
    assert (home.signed, home.issuances) == (5, 1)


async def test_a_lockbox_signer_has_no_census_and_an_external_one_reads_each_home(tmp_path):
    issuer = adapter("http://unused", [])
    assert await issuer.census() is None
    ops = [["sign", 1, 2, 3]]

    async def stats(method, path, body):
        if method == "POST":
            return 500, {"latency_ms": 5.0, "rate": None, "operations": ops}
        return 200, {"latency_ms": 5.0, "rate": None, "operations": ops}

    server = await http.serve(stats, 0)
    try:
        external = adapter("http://unused", [], knobs={"signer": "external",
                                                        "signer_latency_ms": 5.0})
        home = external.homes[0]
        home.signer_port = server.sockets[0].getsockname()[1]
        home.signed, home.issuances = 7, 2
        assert await external.census() == {"signers": [{
            "latency_ms": 5.0, "rate": None, "operations": ops, "signed_responses": 7,
            "refused_answers": 0, "issuances": 2}]}
        # A record that is not this scenario's profile is not this run's signer.
        wrong = adapter("http://unused", [], knobs={"signer": "external",
                                                     "signer_latency_ms": 120.0})
        wrong.homes[0].signer_port = home.signer_port
        with pytest.raises(BenchError) as stranger:
            await wrong.census()
        assert stranger.value.code == "e.input.format.imbu.signer.f"
        await wrong.stop()
        await external.stop()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("knobs", [
    {"signer": "lockbox", "signer_latency_ms": 5.0},
    {"signer": "lockbox", "signer_rate": 10.0},
    {"signer": "hardware"},
    {"signer": "external", "signer_latency_ms": -1.0},
    {"signer": "external", "signer_rate": 0.0},
    {"signer": "external", "signer_rate": float("inf")},
    {"signer": "external", "signer_latency_ms": float("inf")},
])
async def test_a_signer_the_adapter_cannot_build_is_refused_before_anything_starts(knobs):
    ctx = AdapterContext(imbu.ImbuIssuer.declaration, run_dir=None, knobs={**KNOBS, **knobs},
                         peers={}, procs=None, sink=[].append, harness_clock=H,
                         realtime_clock=W)
    with pytest.raises(BenchError) as refused:
        await imbu.ImbuIssuer(ctx).start()
    assert refused.value.code == "e.input.range.imbu.signer.f"


def test_each_home_s_signer_arguments_follow_its_knobs():
    knobs = {**KNOBS, "signer": "external", "signer_latency_ms": 30.0, "signer_rate": "n/a"}
    assert imbu.signer_args(knobs, port=4000) == [
        "--signer", "external", "--signer-port", "4000", "--signer-latency-ms", "30.0"]
    assert imbu.signer_args({**knobs, "signer_latency_ms": "n/a", "signer_rate": 8.0},
                            port=1) == ["--signer", "external", "--signer-port", "1",
                                        "--signer-rate", "8.0"]
    assert imbu.signer_args(KNOBS, port=1) == []


# --- Prefer: wait, the answer budget's refusals, and the budget as knobs (this.i 5k8z0pxe) ---

SIGNED = {"Signature": "sig1=:x:"}
DONE = {"id": "op-1", "state": "done", "milestones": {"accepted": 1, "anchored": 3_000, "witnessed": 4_000,
                                         "published": 5_000, "finished": 6_000},
        "result": {"said": "Esaid"}}
PENDING = {"id": "op-1", "state": "pending", "milestones": {"accepted": 1}}


class FakeHttp:
    """A controller's connection that answers from a script, so each answer's headers and
    each request's headers can be set and read."""

    def __init__(self, answers) -> None:
        self.answers = list(answers)
        self.sent = []

    async def request(self, method, url, *, body=None, headers=None):
        self.sent.append((method, url.split("/", 3)[3], dict(headers or {})))
        status, payload, extra = self.answers.pop(0)
        return Answer(status, json.dumps(payload).encode(), extra)

    async def close(self):
        pass


def scripted(answers, *, knobs=None, sink=None, run_dir=None):
    issuer = adapter("http://imbu", [] if sink is None else sink, knobs=knobs, run_dir=run_dir)
    fake = FakeHttp(answers)
    issuer.homes[0].clients[0].http = fake
    return issuer, fake


@pytest.fixture
def pauses(monkeypatch):
    taken = []

    async def pause(seconds):
        taken.append(seconds)

    monkeypatch.setattr(imbu, "sleep", pause)
    return taken


WAITING = {"poll_ms": "n/a", "wait_s": 25.0}


async def test_a_waiting_client_asks_imbu_to_hold_its_answer_and_needs_no_poll(pauses):
    sink = []
    issuer, fake = scripted([(202, DONE, SIGNED)], knobs=WAITING, sink=sink)
    issued = await issuer.issue("c1", {"aid": "Eholder"})
    assert issued.said == "Esaid"
    # One request, carrying the preference, and one signed answer: the operation came back done.
    assert [(m, p) for m, p, _ in fake.sent] == [("POST", "v1/imbu/issuances")]
    assert fake.sent[0][2]["Prefer"] == "wait=25"
    assert issuer.homes[0].signed == 1
    assert [e.name for e in sink] == ["accepted", "anchored", "witnessed", "published"]
    assert pauses == []


async def test_a_waiting_client_reads_a_still_pending_operation_again_at_once(pauses):
    issuer, fake = scripted([(202, {"operation": PENDING}, SIGNED), (200, PENDING, SIGNED),
                             (200, DONE, SIGNED)], knobs=WAITING)
    await issuer.issue("c1", {"aid": "Eholder"})
    assert [(m, p) for m, p, _ in fake.sent] == [
        ("POST", "v1/imbu/issuances"), ("GET", "v1/imbu/operations/op-1"),
        ("GET", "v1/imbu/operations/op-1")]
    assert all(h["Prefer"] == "wait=25" for _, _, h in fake.sent)
    assert pauses == []


async def test_a_waiting_client_still_refuses_a_failed_operation(pauses):
    issuer, _ = scripted([(202, {"id": "op-1", "state": "failed", "error": "no"}, SIGNED)],
                         knobs=WAITING)
    with pytest.raises(BenchError) as failed:
        await issuer.issue("c1", {"aid": "Eholder"})
    assert failed.value.code == "e.party.imbu.operation.f"


async def test_a_polling_client_sends_no_preference_and_reads_after_the_202(pauses):
    issuer, fake = scripted([(202, DONE, SIGNED), (200, DONE, SIGNED)])
    await issuer.issue("c1", {"aid": "Eholder"})
    # The baselines' client: the 202 is never taken as the answer, so counts stay comparable.
    assert [m for m, _, _ in fake.sent] == ["POST", "GET"]
    assert all("Prefer" not in h for _, _, h in fake.sent)


async def test_a_refused_answer_is_asked_again_after_its_retry_after_with_the_same_key(pauses):
    refusal = (429, {"code": "e.grant.quota.answers.r"}, {"retry-after": "3"})
    issuer, fake = scripted([refusal, (202, PENDING, SIGNED), refusal, (200, DONE, SIGNED)],
                            knobs={"operation_timeout_s": 10.0})
    await issuer.issue("c1", {"aid": "Eholder"})
    assert [m for m, _, _ in fake.sent] == ["POST", "POST", "GET", "GET"]
    assert fake.sent[0][2]["Idempotency-Key"] == fake.sent[1][2]["Idempotency-Key"] == "c1"
    assert pauses == [3, 3]
    home = issuer.homes[0]
    # A refusal is the one answer imbu does not sign, so it is counted apart.
    assert (home.signed, home.refused) == (2, 2)


async def test_a_refusal_whose_wait_outlasts_the_operation_is_a_timeout(pauses):
    issuer, _ = scripted([(429, {}, {"Retry-After": "5"})])
    with pytest.raises(BenchError) as late:
        await issuer.issue("c1", {"aid": "Eholder"})
    assert late.value.code == "e.env.imbu.operation.r"
    assert pauses == []


PROBLEM = {"Content-Type": "application/problem+json"}
SATURATED = (503, {"code": "e.env.saturated.r"}, {**PROBLEM, "Retry-After": "4"})


async def test_a_saturated_home_is_asked_again_after_its_retry_after_with_the_same_key(pauses):
    # imbu 44309be answers 503 e.env.saturated.r with Retry-After when a home holds as much
    # anchoring work as it finishes in its horizon (imbu @66fmushs); a 503 is never signed.
    issuer, fake = scripted([SATURATED, SATURATED, (202, PENDING, SIGNED), (200, DONE, SIGNED)],
                            knobs={"operation_timeout_s": 10.0})
    issued = await issuer.issue("c1", {"aid": "Eholder"})
    assert issued.said == "Esaid"
    assert [m for m, _, _ in fake.sent] == ["POST", "POST", "POST", "GET"]
    assert {h["Idempotency-Key"] for m, _, h in fake.sent if m == "POST"} == {"c1"}
    assert pauses == [4, 4]
    home = issuer.homes[0]
    # Counted apart from the answer budget's refusals, which are the controller's, not the home's.
    assert (home.signed, home.refused, home.saturated) == (2, 0, 2)
    assert home.issuances == 1


async def test_a_saturated_home_whose_wait_outlasts_the_operation_is_a_timeout(pauses):
    issuer, _ = scripted([(503, {"code": "e.env.saturated.r"}, {**PROBLEM, "Retry-After": "5"})])
    with pytest.raises(BenchError) as late:
        await issuer.issue("c1", {"aid": "Eholder"})
    assert late.value.code == "e.env.imbu.operation.r"
    assert pauses == []
    assert issuer.homes[0].saturated == 1


@pytest.mark.parametrize("extra", [{}, {"Retry-After": "soon"}])
async def test_a_503_without_a_readable_retry_after_is_refused(pauses, extra):
    issuer, _ = scripted([(503, {"code": "e.env.saturated.r"}, {**PROBLEM, **extra})])
    with pytest.raises(BenchError) as refused:
        await issuer.issue("c1", {"aid": "Eholder"})
    assert refused.value.code == "e.party.imbu.refused.f"
    assert issuer.homes[0].saturated == 0


@pytest.mark.parametrize("body", [{"code": "e.state.conflict.f"}, {}, "not json"])
async def test_a_503_that_is_not_saturation_is_refused_not_retried(pauses, body):
    issuer, fake = scripted([(503, body, {**PROBLEM, "Retry-After": "1"}), (202, DONE, SIGNED)])
    with pytest.raises(BenchError) as refused:
        await issuer.issue("c1", {"aid": "Eholder"})
    assert refused.value.code == "e.party.imbu.refused.f"
    assert len(fake.sent) == 1 and pauses == [] and issuer.homes[0].saturated == 0


SATURATION = b'{"code": "e.env.saturated.r"}'


@pytest.mark.parametrize("body, headers, code", [
    (b"<html>busy</html>", PROBLEM, None),
    (b'"text"', PROBLEM, None),
    (SATURATION, PROBLEM, "e.env.saturated.r"),
    (SATURATION, {"content-type": "Application/Problem+JSON; charset=utf-8"}, "e.env.saturated.r"),
    # imbu sends its refusals as application/problem+json (imbu problem.MEDIA_TYPE); a body of
    # any other type is not a problem document, whatever it says (land review p2.1).
    (SATURATION, {"Content-Type": "text/html"}, None),
    (SATURATION, {"Content-Type": "application/problem+jsonx"}, None),
    (SATURATION, {}, None),
    # A document naming its code twice is ambiguous, so it names none (p2.2).
    (b'{"code": "e.state.conflict.f", "code": "e.env.saturated.r"}', PROBLEM, None),
    # Nesting past the parser's depth is unreadable, not a crash (p2.3).
    (b'{"a":' * 100_000 + b"1" + b"}" * 100_000, PROBLEM, None),
    # Strict JSON only: no NaN or Infinity, and UTF-8 as RFC 8259 requires (p3.2).
    (b'{"code": "e.env.saturated.r", "n": NaN}', PROBLEM, None),
    (b'{"code": "e.env.saturated.r", "n": 1e309}', PROBLEM, None),
    ('{"code": "e.env.saturated.r"}'.encode("utf-16"), PROBLEM, None),
    # A refusal is a few hundred bytes; past the bound it is not read at all (p3.3).
    (b'{"code": "e.env.saturated.r", "pad": "' + b"x" * imbu.PROBLEM_LIMIT + b'"}', PROBLEM,
     None),
], ids=["html", "string", "problem", "parameters", "text/html", "lookalike", "no-type",
        "two-codes", "deep", "nan", "overflow", "utf-16", "oversized"])
def test_problem_code_reads_only_a_problem_documents_one_code(body, headers, code):
    assert imbu.problem_code(Answer(503, body, headers)) == code


async def test_a_saturation_code_in_another_media_type_is_refused_not_retried(pauses):
    issuer, fake = scripted([(503, {"code": "e.env.saturated.r"},
                              {"Content-Type": "text/html", "Retry-After": "1"}),
                             (202, DONE, SIGNED)])
    with pytest.raises(BenchError) as refused:
        await issuer.issue("c1", {"aid": "Eholder"})
    assert refused.value.code == "e.party.imbu.refused.f"
    assert len(fake.sent) == 1 and pauses == []


async def test_a_zero_retry_after_is_waited_as_one_second(pauses):
    issuer, _ = scripted([(503, {"code": "e.env.saturated.r"}, {**PROBLEM, "Retry-After": "0"}),
                          (429, {}, {"Retry-After": "0"}), (202, DONE, SIGNED),
                          (200, DONE, SIGNED)], knobs={"operation_timeout_s": 10.0})
    await issuer.issue("c1", {"aid": "Eholder"})
    assert pauses == [1, 1]


@pytest.mark.parametrize("status", [500, 502, 504])
async def test_another_5xx_is_not_retried_even_with_a_retry_after(pauses, status):
    issuer, fake = scripted([(status, {}, {"Retry-After": "1"})])
    with pytest.raises(BenchError) as refused:
        await issuer.issue("c1", {"aid": "Eholder"})
    assert refused.value.code == "e.party.imbu.refused.f"
    assert len(fake.sent) == 1 and pauses == []


async def test_each_homes_answers_are_written_beside_the_run_when_it_stops(pauses, tmp_path):
    refusal = (429, {}, {"Retry-After": "1"})
    issuer, _ = scripted([SATURATED, refusal, (202, PENDING, SIGNED), (200, DONE, SIGNED)],
                         knobs={"operation_timeout_s": 10.0}, run_dir=tmp_path)
    await issuer.issue("c1", {"aid": "Eholder"})
    await issuer.stop()
    record = json.loads((tmp_path / "imbu-answers.json").read_text())
    assert record == {"homes": [{"home": 0, "signed": 2, "refused_budget": 1,
                                 "refused_saturated": 1, "issuances": 1}]}
    # Not under imbu/, whose bytes are the stock's footprint.
    assert not (tmp_path / "imbu").exists()


async def test_no_answers_record_is_written_without_a_run_directory(pauses):
    issuer, _ = scripted([(202, DONE, SIGNED), (200, DONE, SIGNED)])
    await issuer.issue("c1", {"aid": "Eholder"})
    await issuer.stop()  # nowhere to write, and nothing raised


@pytest.mark.parametrize("extra", [{}, {"Retry-After": "soon"}, {"Retry-After": "1.5"},
                                   {"Retry-After": ""}])
async def test_a_refusal_without_a_readable_retry_after_is_refused(pauses, extra):
    issuer, _ = scripted([(429, {}, extra)])
    with pytest.raises(BenchError) as refused:
        await issuer.issue("c1", {"aid": "Eholder"})
    assert refused.value.code == "e.party.imbu.refused.f"
    assert issuer.homes[0].refused == 0


@pytest.mark.parametrize("value, seconds", [("1", 1), ("0", 0), ("120", 120), (" 2", None),
                                            ("-1", None), ("١", None), ("999999", 999999),
                                            ("1000000", None),
                                            pytest.param("9" * 4301, None, id="4301-digits")])
def test_retry_after_is_whole_ascii_seconds(value, seconds):
    assert imbu.retry_after(Answer(429, b"", {"Retry-After": value})) == seconds


@pytest.mark.parametrize("knobs", [
    {"poll_ms": "n/a", "wait_s": "n/a"},
    {"poll_ms": 50.0, "wait_s": 25.0},
    {"poll_ms": 0.0},
    {"poll_ms": float("inf")},
    {"poll_ms": "n/a", "wait_s": 0.0},
    {"poll_ms": "n/a", "wait_s": 26.0},
    {"poll_ms": "n/a", "wait_s": 2.5},
])
async def test_a_client_that_neither_polls_nor_waits_or_both_is_refused_before_it_starts(knobs):
    ctx = AdapterContext(imbu.ImbuIssuer.declaration, run_dir=None, knobs={**KNOBS, **knobs},
                         peers={}, procs=None, sink=[].append, harness_clock=H,
                         realtime_clock=W)
    with pytest.raises(BenchError) as refused:
        await imbu.ImbuIssuer(ctx).start()
    assert refused.value.code == "e.input.range.imbu.wait.f"


@pytest.mark.parametrize("knobs", [
    {"answer_rate": 0.0}, {"answer_rate": -2.0}, {"answer_rate": float("inf")},
    {"answer_rate": float("nan")}, {"answer_burst": 0}, {"answer_burst": 2**32 + 1},
])
async def test_a_budget_imbu_would_refuse_is_refused_before_it_starts(knobs):
    ctx = AdapterContext(imbu.ImbuIssuer.declaration, run_dir=None, knobs={**KNOBS, **knobs},
                         peers={}, procs=None, sink=[].append, harness_clock=H,
                         realtime_clock=W)
    with pytest.raises(BenchError) as refused:
        await imbu.ImbuIssuer(ctx).start()
    assert refused.value.code == "e.input.range.imbu.budget.f"


def test_each_home_is_given_the_scenario_s_budget():
    assert imbu.budget_args({**KNOBS, "answer_rate": 1e6, "answer_burst": 2**32}) == [
        "--answer-rate", "1000000.0", "--answer-burst", str(2**32)]


async def test_the_census_counts_the_refusals_beside_the_signed_answers(tmp_path):
    async def stats(method, path, body):
        return 200, {"latency_ms": None, "rate": None, "operations": []}

    server = await http.serve(stats, 0)
    try:
        issuer = adapter("http://unused", [], knobs={"signer": "external"})
        home = issuer.homes[0]
        home.signer_port = server.sockets[0].getsockname()[1]
        home.signed, home.refused, home.issuances = 3, 4, 1
        census = await issuer.census()
        assert census["signers"][0]["refused_answers"] == 4
        await issuer.stop()
    finally:
        server.close()
        await server.wait_closed()


def test_a_202_whose_body_is_not_an_operation_document_offers_none():
    # A waiting client then reads the operation instead of taking the 202 as its answer.
    assert imbu.operation_document(Answer(202, b"not json", {})) is None
    assert imbu.operation_document(answer([1])) is None
    assert imbu.operation_document(answer({"operation": {"id": "a"}})) == {"id": "a"}
    assert imbu.operation_document(answer({"id": "b"})) == {"id": "b"}
