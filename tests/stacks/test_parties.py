"""The holder, verifier and issuer processes' refusal paths, driven directly with stand-ins for
heti's lockbox and facet; the happy paths run for real in test_stack.py."""

import base64
import json
from types import SimpleNamespace

import pytest

from keri_bench.adapters.base import AdapterContext
from keri_bench.stacks import adapters, holder, issuer, verifier


class Refused(Exception):
    code = "e.state.conflict.acdc.revoked.f"


class Facet:
    aid, oobis = "Eme", ["http://w/oobi/Eme"]

    def __init__(self, admit):
        self._admit = admit

    async def admit(self, *, grant, recency):
        if isinstance(self._admit, Exception):
            raise self._admit
        return self._admit


async def test_the_holder_refuses_what_it_cannot_hold():
    held = holder.Holder(None, Facet(SimpleNamespace(verdict=None)))  # no acdc: a HeldGrant
    assert (await held.handle("GET", "/identity", b""))[1] == {"aid": "Eme",
                                                              "oobi": "http://w/oobi/Eme"}
    assert (await held.handle("POST", "/admit/c1", b""))[0] == 404  # nothing delivered
    await held.handle("POST", "/deliver/c1", b"g")
    status, body = await held.handle("POST", "/admit/c1", b"")
    assert (status, body["code"]) == (409, "held")
    held.facet = Facet(ValueError("bad grant"))
    status, body = await held.handle("POST", "/admit/c1", b"")
    assert (status, body["code"]) == (409, "ValueError")
    assert (await held.handle("POST", "/present/c1", b"{}"))[0] == 404


class Box:
    def __init__(self, verdict):
        self.verdict = verdict

    async def add_other(self, *, oobi):
        pass

    async def verify_current_acdc(self, **kw):
        if isinstance(self.verdict, Exception):
            raise self.verdict
        return self.verdict


async def test_the_verifier_names_refusals_and_judges_status_from_the_observer(monkeypatch):
    monkeypatch.setattr(verifier, "disclosures", lambda grant: None)
    admitted = SimpleNamespace(acdc=b"acdc")
    ok = SimpleNamespace(state="issued", said="Es", registry="Er")
    judge = verifier.Verifier(Box(ok), Facet(admitted), None, 30.0)
    assert (await judge.handle("GET", "/identity", b""))[0] == 200
    assert (await judge.handle("GET", "/nowhere", b""))[0] == 404
    request = json.dumps({"oobi": "o", "grant": base64.b64encode(b"g").decode()}).encode()
    assert (await judge.handle("POST", "/verify/c1", request))[1]["valid"]
    assert (await judge.handle("GET", "/status/Es", b""))[1] == {"status": "valid"}
    assert (await judge.handle("GET", "/status/Eother", b""))[1] == {"status": "unknown"}
    judge.box = Box(Refused())
    assert (await judge.handle("GET", "/status/Es", b""))[1] == {"status": "revoked"}
    judge.box = Box(ValueError("gap"))
    assert (await judge.handle("GET", "/status/Es", b""))[1] == {"status": "unknown"}
    judge.box = Box(SimpleNamespace(state="unknown", said="Es", registry="Er"))
    assert (await judge.handle("GET", "/status/Es", b""))[1] == {"status": "unknown"}
    status, body = await judge.handle("POST", "/verify/c2", b'{"oobi": "o", "grant": "!!"}')
    assert status == 409 and body["valid"] is False


async def test_a_registrar_that_acknowledges_another_snapshot_is_refused(monkeypatch):
    import fiki

    class Answer:
        def raise_for_status(self):
            pass

        def json(self):
            return {"digest": "other"}

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def post(self, *a, **kw):
            return Answer()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    publisher = issuer.RegistrarPublisher("http://127.0.0.1:1", key=fiki.Key.generate())
    snapshot = SimpleNamespace(registry="Er", issuer="Ei", digest="mine", chain=b"c", kel=b"k")
    with pytest.raises(issuer.AcknowledgementMismatch):
        await publisher.publish(snapshot)


async def test_waiting_for_a_listener_gives_up_at_its_deadline():
    from keri_bench.procs import free_port
    with pytest.raises(OSError):
        await adapters.listening(f"http://127.0.0.1:{free_port()}", timeout=0.1)


async def test_a_verifier_adapter_reports_an_unissued_credential_as_unknown(tmp_path):
    ctx = AdapterContext(adapters.HetiVerifier.declaration, run_dir=tmp_path, knobs={},
                         peers={}, procs=None, sink=[].append, harness_clock="h",
                         realtime_clock="r")
    party = adapters.HetiVerifier(ctx)
    assert await party.status("c1") == {"status": "unknown", "said": None}
    await party.stop()  # never started: nothing to close


async def test_a_listener_that_dies_before_it_starts_stops_the_issuer():
    class Dead:
        started = False

        async def serve(self):
            return None

    with pytest.raises(SystemExit):
        await issuer.start_listener(Dead())


async def test_waiting_for_a_listener_retries_until_it_binds():
    import asyncio

    from keri_bench.procs import free_port
    port = free_port()

    async def later():
        await asyncio.sleep(0.15)
        return await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", port)

    binding = asyncio.create_task(later())
    await adapters.listening(f"http://127.0.0.1:{port}", timeout=5)
    server = await binding
    server.close()


async def test_a_refusing_holder_endpoint_is_a_coded_stage_failure(tmp_path):
    from keri_bench.adapters.base import Issued
    from keri_bench.errors import BenchError
    from keri_bench.http import HttpClient
    from keri_bench.synthetic import http
    from keri_bench.vocabulary import Role

    async def refuse(method, path, body):
        return 409, {"code": "nope"}

    server = await http.serve(refuse, 0)
    url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    ctx = AdapterContext(adapters.HetiHolder.declaration, run_dir=tmp_path, knobs={},
                         peers={Role.HOLDER: {"api": url}}, procs=None, sink=[].append,
                         harness_clock="h", realtime_clock="r")
    party = adapters.HetiHolder(ctx)
    party.client = HttpClient()
    try:
        with pytest.raises(BenchError) as refused:
            await party.receive(Issued("c1", "E1", b"g", False))
        assert refused.value.code == "e.party.stack.refused.f"
    finally:
        await party.stop()
        server.close()


@pytest.mark.parametrize("answer", [{"valid": False, "said": "E1"}, {"valid": True, "said": "E2"}])
async def test_a_verifier_that_does_not_accept_this_credential_is_a_stage_failure(tmp_path,
                                                                                   answer):
    from keri_bench.errors import BenchError
    from keri_bench.http import HttpClient
    from keri_bench.synthetic import http
    from keri_bench.vocabulary import Role

    async def reply(method, path, body):
        return 200, answer

    server = await http.serve(reply, 0)
    url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    ctx = AdapterContext(adapters.HetiVerifier.declaration, run_dir=tmp_path,
                         knobs={"deadline_s": 1.0, "retry_ms": 1.0},
                         peers={Role.VERIFIER: {"api": url}}, procs=None, sink=[].append,
                         harness_clock="h", realtime_clock="r", shared={"said": {"c1": "E1"}})
    party = adapters.HetiVerifier(ctx)
    party.client = HttpClient()
    try:
        with pytest.raises(BenchError):
            await party.verify("c1", {"grant": "g", "oobi": "o"})
    finally:
        await party.stop()
        server.close()


@pytest.mark.parametrize("answer, said", [([1, 2], "E1"), ({"valid": True, "said": "E1"}, None)])
async def test_a_malformed_answer_or_an_unknown_issuance_is_not_verification(tmp_path, answer,
                                                                             said):
    from keri_bench.errors import BenchError
    from keri_bench.http import HttpClient
    from keri_bench.synthetic import http
    from keri_bench.vocabulary import Role

    async def reply(method, path, body):
        return 200, answer

    server = await http.serve(reply, 0)
    url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    ctx = AdapterContext(adapters.HetiVerifier.declaration, run_dir=tmp_path,
                         knobs={"deadline_s": 1.0, "retry_ms": 1.0},
                         peers={Role.VERIFIER: {"api": url}}, procs=None, sink=[].append,
                         harness_clock="h", realtime_clock="r", shared={"said": {"c1": said}})
    party = adapters.HetiVerifier(ctx)
    party.client = HttpClient()
    try:
        with pytest.raises(BenchError) as refused:
            await party.verify("c1", {"grant": "g", "oobi": "o"})
        assert refused.value.code == "e.party.stack.refused.f"
    finally:
        await party.stop()
        server.close()
