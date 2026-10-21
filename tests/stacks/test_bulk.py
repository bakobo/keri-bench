"""The imbu bulk issuer's refusal paths, against stand-ins for imbu and the citizens process;
test_stack.py runs the ceremony for real (this.i 4f987y88)."""

import base64

import fiki
import pytest

from keri_bench.adapters.base import AdapterContext
from keri_bench.errors import BenchError
from keri_bench.stacks import bulk, citizens, issuer
from keri_bench.stacks.imbu import Client, Home
from keri_bench.synthetic import http

KNOBS = {"toad": 1, "poll_ms": 1.0, "operation_timeout_s": 2.0, "homes": 1, "clients": 1,
         "copies": 1, "pool": 0}


def context(**knobs):
    return AdapterContext(bulk.ImbuBulkIssuer.declaration, run_dir=None,
                          knobs={**KNOBS, **knobs}, peers={}, procs=None, sink=[].append,
                          harness_clock="h", realtime_clock="r")


@pytest.mark.parametrize("knobs", [{"homes": 0}, {"clients": 0}, {"copies": 0},
                                   {"copies": 51}, {"pool": -1}])
async def test_a_deployment_the_ceremony_cannot_run_is_refused_before_it_starts(knobs):
    with pytest.raises(BenchError) as refused:
        await bulk.ImbuBulkIssuer(context(**knobs)).start()
    assert refused.value.code == "e.input.range.imbu.deployment.f"


@pytest.fixture
async def stands():
    """imbu refusing everything, and a citizens process that answers what is asked."""
    async def imbu(method, path, body):
        return 409, {"code": "e.refused"}

    async def people(method, path, body):
        if path.startswith("/ceremony/"):
            return 200, {"iar": base64.b64encode(b"iar").decode(), "said": "Eiar"}
        return 200, {"headers": {"Signature": "sig1=:x:"}}

    servers = [await http.serve(imbu, 0), await http.serve(people, 0)]
    urls = [f"http://127.0.0.1:{s.sockets[0].getsockname()[1]}" for s in servers]
    yield urls
    for server in servers:
        server.close()
        await server.wait_closed()


async def test_a_refused_resolution_pool_or_ceremony_is_a_coded_error(stands):
    imbu_url, people_url = stands
    adapter = bulk.ImbuBulkIssuer(context(pool=5))
    adapter.citizens_url = people_url
    adapter.cast = {"proofer": {"aid": "Ep", "oobi": "http://w/oobi/Ep"}, "citizens": []}
    adapter.homes = [Home(0, imbu_url, [Client(None, citizen=0)],
                          operator=Client(fiki.Key.generate()),
                          agent={"said": "Ea", "acdc": "", "chain": ""})]
    try:
        with pytest.raises(BenchError) as refused:
            await adapter.meet({})
        assert refused.value.code == "e.party.imbu.refused.f"
        with pytest.raises(BenchError) as ceremony:
            await adapter.issue("c1", {})
        assert ceremony.value.code == "e.party.imbu.refused.f"
        with pytest.raises(BenchError) as revoked:
            await adapter.revoke("c1")
        assert revoked.value.code == "e.feature.unsupported.imbu.bulk.revoke.f"
    finally:
        await adapter.stop()


async def test_the_citizens_process_answers_only_its_routes():
    people = citizens.Citizens(None, [object()], token="t")
    assert (await people.handle("POST", "/sign/7", b'{"token": "t"}'))[0] == 404
    assert (await people.handle("POST", "/wave/0", b'{"token": "t"}'))[0] == 404
    assert (await people.handle("GET", "/sign/0", b""))[0] == 404


@pytest.mark.parametrize("body", [b"", b"not json", b"[1]", b'{"token": "wrong"}',
                                  b'{"method": "GET"}', '{"token": "\u00e9"}'.encode(),
                                  b'{"token": "\\ud800"}'])
async def test_the_citizens_process_signs_and_proofs_only_for_its_token(body):
    """Hostile review of bench-baseline-2, finding 1: another process on a shared host must not
    get a citizen's signature on a request of its choosing."""
    people = citizens.Citizens(None, [object()], token="t")
    for route in ("/sign/0", "/ceremony/0"):
        status, answer = await people.handle("POST", route, body)
        assert (status, answer["code"]) == (403, "e.auth.citizens.token.f")


def test_a_bulk_issuer_without_a_proofing_agent_is_a_usage_error(capsys):
    with pytest.raises(SystemExit):
        issuer.main(["--witness", "w", "--toad", "1", "--registrar-url", "r", "--controller",
                     "c", "--control-port", "1", "--bulk"])
    assert "--proofer" in capsys.readouterr().err


async def test_the_census_carries_each_pool_provisioning_imbu_stamped():
    adapter = bulk.ImbuBulkIssuer(context())
    adapter.pool = [{"home": 0, "registries": 5, "accepted_ns": 1, "finished_ns": 2}]
    assert await adapter.census() == {"signers": [], "pool": adapter.pool}
    await adapter.stop()



@pytest.mark.parametrize("result", [None, {}, {"copies": []}, {"copies": [{"core": {}}]},
                                    {"copies": [{"core": {"said": "Ec"},
                                                 "residence": {"said": 7}}]},
                                    {"copies": [{"core": {"said": "Ec"},
                                                 "residence": {"said": "Ec"}}]}])
def test_a_ceremony_whose_result_does_not_name_every_copy_is_refused(result):
    """Finding 2: milestone stamps are not evidence that the credentials exist."""
    with pytest.raises(BenchError) as refused:
        bulk.copies_issued({"result": result}, copies=1)
    assert refused.value.code == "e.input.format.imbu.operation.f"
    assert bulk.copies_issued({"result": {"copies": [
        {"core": {"said": "Ec"}, "residence": {"said": "Er"}}]}}, copies=1) == "Ec"
