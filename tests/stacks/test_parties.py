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


async def test_the_verifier_names_refusals_and_judges_status_from_a_fresh_presentation(
        monkeypatch):
    monkeypatch.setattr(verifier, "presented", lambda grant: ["Es"])
    ok = SimpleNamespace(state="issued", said="Es", registry="Er")
    admitted = SimpleNamespace(acdc=b"acdc", blinders=None, verdict=ok)
    judge = verifier.Verifier(Box(ok), Facet(admitted), None, 30.0)
    assert (await judge.handle("GET", "/identity", b""))[0] == 200
    assert (await judge.handle("GET", "/nowhere", b""))[0] == 404
    request = json.dumps({"oobi": "o", "grant": base64.b64encode(b"g").decode()}).encode()
    assert (await judge.handle("POST", "/verify/c1", request))[1]["valid"]

    async def status(said="Es"):
        return (await judge.handle("POST", f"/status/{said}", request))[1]["status"]

    assert await status() == "valid"
    assert await status("Eother") == "unknown"  # never verified here
    judge.facet = Facet(SimpleNamespace(acdc=b"acdc", blinders=None,
                                        verdict=SimpleNamespace(said="Eanother")))
    assert await status() == "unknown"  # a presentation of some other credential
    judge.facet = Facet(admitted)
    judge.box = Box(Refused())
    assert await status() == "revoked"
    judge.box = Box(ValueError("gap"))
    assert await status() == "unknown"
    judge.box = Box(SimpleNamespace(state="unknown", said="Es", registry="Er"))
    assert await status() == "unknown"
    status_, body = await judge.handle("POST", "/verify/c2", b'{"oobi": "o", "grant": "!!"}')
    assert status_ == 409 and body["valid"] is False
    for malformed in (b"{", b"null", b"[]", b"42", b"{}"):
        assert (await judge.handle("POST", "/status/Es", malformed))[0] == 400
    # A presentation of another credential, even one admit refuses as revoked, says nothing of
    # this one.
    monkeypatch.setattr(verifier, "presented", lambda grant: ["Eanother"])
    judge.box = Box(Refused())
    assert await status() == "unknown"


def test_a_grant_names_the_credentials_it_conveys_or_nothing():
    assert verifier.presented(b"not a grant") is None


async def test_the_holder_refuses_a_re_presentation_it_cannot_make():
    class Source:
        async def read(self, registry):
            raise ValueError("the Observer holds no chain for " + registry)

    held = holder.Holder(None, None, Source())
    held.held["c1"] = SimpleNamespace(verdict=SimpleNamespace(registry="Er"))
    status, body = await held.handle("POST", "/represent/c1", b'{"verifier": "Ev"}')
    assert (status, body["code"]) == (409, "ValueError")


async def test_a_holder_that_cannot_present_again_leaves_the_status_unknown(tmp_path):
    class Holder:
        async def represent(self, credential, verifier):
            raise adapters.BenchError("e.party.stack.refused.f", "no")

    ctx = AdapterContext(adapters.HetiVerifier.declaration, run_dir=tmp_path, knobs={},
                         peers={}, procs=None, sink=[].append, harness_clock="h",
                         realtime_clock="r", shared={"said": {"c1": "Es"}, "holder": Holder()})
    party = adapters.HetiVerifier(ctx)
    party.me = {"aid": "Ev"}
    assert await party.status("c1") == {"status": "unknown", "said": "Es"}


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


@pytest.mark.parametrize("answer, said", [([1, 2], "E1"), ({"valid": True, "said": "E1"}, None),
                                          ({"valid": True, "said": ""}, "")])
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


async def test_a_refused_verification_is_retried_until_it_is_accepted(tmp_path):
    from keri_bench.http import HttpClient
    from keri_bench.synthetic import http
    from keri_bench.vocabulary import Role

    answers = iter([(409, {"valid": False, "code": "e.proof.binding.f"}),
                    (200, {"valid": True, "said": "E1"})])

    async def reply(method, path, body):
        return next(answers)

    server = await http.serve(reply, 0)
    url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    sink = []
    ctx = AdapterContext(adapters.HetiVerifier.declaration, run_dir=tmp_path,
                         knobs={"deadline_s": 5.0, "retry_ms": 1.0},
                         peers={Role.VERIFIER: {"api": url}}, procs=None, sink=sink.append,
                         harness_clock="h", realtime_clock="r", shared={"said": {"c1": "E1"}})
    party = adapters.HetiVerifier(ctx)
    party.client = HttpClient()
    try:
        assert await party.verify("c1", {"grant": "g", "oobi": "o"}) is True
        assert sink[0].attrs["attempts"] == 2
    finally:
        await party.stop()
        server.close()


# --- restart from disk (this.i 5ed29j5w) ----------------------------------------------------

class Admits:
    """A facet whose admit hands back a held credential and counts how often it was asked."""

    aid, oobis = "Eme", ["http://w/oobi/Eme"]

    def __init__(self):
        self.admits = 0

    async def admit(self, *, grant, recency):
        self.admits += 1
        return SimpleNamespace(acdc=b"acdc:" + grant, tel=b"tel", blinders=b"bl",
                               verdict=SimpleNamespace(said="Es", registry="Er"))

    def grant(self, *, acdc, tel, recipient, blinders=None):
        return acdc + b"->" + recipient.encode()


async def test_a_restarted_holder_presents_what_it_held_before_the_crash(tmp_path):
    before = holder.Holder(None, Admits(), spool=tmp_path)
    assert (await before.handle("POST", "/deliver/c1", b"g1"))[0] == 202
    assert (await before.handle("POST", "/admit/c1", b""))[0] == 200
    assert (tmp_path / "c1").read_bytes() == b"g1"
    # A new process over the same spool: nothing in memory, the grant on disk.
    facet = Admits()
    after = holder.Holder(None, facet, spool=tmp_path)
    status, body = await after.handle("POST", "/present/c1", b'{"verifier": "Ev"}')
    assert status == 200 and base64.b64decode(body["grant"]) == b"acdc:g1->Ev"
    assert facet.admits == 1
    await after.handle("POST", "/present/c1", b'{"verifier": "Ev"}')
    assert facet.admits == 1  # admitted again once, then remembered
    # A grant delivered before the crash and never admitted can still be admitted.
    await before.handle("POST", "/deliver/c2", b"g2")
    assert (await holder.Holder(None, Admits(), spool=tmp_path).handle(
        "POST", "/admit/c2", b""))[0] == 200


async def test_a_restarted_holder_with_nothing_spooled_or_a_refused_readmit_presents_nothing(
        tmp_path):
    empty = holder.Holder(None, Admits(), spool=tmp_path)
    assert (await empty.handle("POST", "/present/c9", b'{"verifier": "Ev"}'))[0] == 404
    (tmp_path / "c3").write_bytes(b"g3")
    refusing = holder.Holder(None, Facet(ValueError("revoked meanwhile")), spool=tmp_path)
    assert (await refusing.handle("POST", "/represent/c3", b'{"verifier": "Ev"}'))[0] == 404
    unheld = holder.Holder(None, Facet(SimpleNamespace(verdict=None)), spool=tmp_path)
    assert (await unheld.handle("POST", "/present/c3", b'{"verifier": "Ev"}'))[0] == 404


async def test_a_holder_refuses_to_spool_a_credential_name_that_is_not_a_plain_name(tmp_path):
    held = holder.Holder(None, Admits(), spool=tmp_path)
    status, body = await held.handle("POST", "/deliver/../escape", b"g")
    assert (status, body["code"]) == (400, "e.input.format.credential.f")
    assert not (tmp_path.parent / "escape").exists()
    # Without a spool, nothing touches the disk and any name is only a key.
    assert (await holder.Holder(None, Admits()).handle("POST", "/deliver/a.b", b"g"))[0] == 202


async def test_a_restarted_verifier_still_knows_what_it_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier, "presented", lambda grant: ["Es"])
    ok = SimpleNamespace(state="issued", said="Es", registry="Er")
    admitted = SimpleNamespace(acdc=b"a", blinders=None, verdict=ok)
    first = verifier.Verifier(Box(ok), Facet(admitted), None, 30.0, spool=tmp_path)
    request = {"oobi": "http://w/oobi/Eh", "grant": base64.b64encode(b"g").decode()}
    assert (await first.verify(request))[0] == 200
    again = verifier.Verifier(Box(ok), Facet(admitted), None, 30.0, spool=tmp_path)
    assert again.known == {"Es": "Er"}
    assert await again.status("Es", request) == "valid"
    assert verifier.Verifier(Box(ok), Facet(admitted), None, 30.0).known == {}
