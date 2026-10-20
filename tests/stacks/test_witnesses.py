"""Witnesses that advertise the harness's proxy, so receipts pass through it."""

import sys

from keri_bench.http import HttpClient
from keri_bench.procs import ProcessController, free_port
from keri_bench.proxy import Link

WITNESSES = (sys.executable, "-m", "keri_bench.stacks.witnesses")


async def test_a_resolved_oobi_names_the_proxy_and_traffic_goes_through_it(tmp_path):
    seen = []
    ports = [free_port(), free_port()]
    links = [Link(f"w{i}", f"http://127.0.0.1:{port}", on_exchange=seen.append)
             for i, port in enumerate(ports)]
    for link in links:
        await link.start()
    procs = ProcessController(tmp_path)
    args = []
    for port, link in zip(ports, links):
        args += ["--port", str(port), "--advertise", link.url]
    client = HttpClient(timeout_s=30)
    try:
        process = await procs.start("witnesses", WITNESSES, tuple(args), timeout=120)
        oobis, aids = process.announced["oobis"], process.announced["aids"]
        assert len(oobis) == 2 and len(aids) == 2
        assert oobis[0].startswith(links[0].url)
        answer = await client.get(oobis[0])
        assert answer.status == 200
        # The location record the witness signs names the proxy, not the port it binds.
        assert links[0].url.encode() in answer.body
        assert f":{ports[0]}".encode() not in answer.body
        assert [e.link for e in seen] == ["w0"]
    finally:
        await client.close()
        await procs.stop_all()
        for link in links:
            await link.stop()


def test_the_witness_process_refuses_mismatched_ports_and_addresses(capsys):
    import pytest

    from keri_bench.stacks import witnesses
    with pytest.raises(SystemExit):
        witnesses.main(["--port", "1", "--port", "2", "--advertise", "http://x"])
    assert "one --advertise per --port" in capsys.readouterr().err


async def test_a_killed_witness_restarts_as_the_same_witness_with_its_store(tmp_path):
    """this.i 5ed29j5w: a restarted witness must be the one every KEL names, and must still
    hold what it held, or a crash test measures re-provisioning instead of recovery."""
    port = free_port()
    link = Link("w1", f"http://127.0.0.1:{port}")
    await link.start()
    procs = ProcessController(tmp_path)
    store = tmp_path / "witnesses"
    args = ("--port", str(port), "--advertise", link.url, "--seed", "ab" * 14, "--first", "1",
            "--store", str(store))
    client = HttpClient(timeout_s=30)
    try:
        before = (await procs.start("w1", WITNESSES, args, timeout=120)).announced
        assert before["oobis"][0].startswith(link.url)
        assert any(store.rglob("*")), "the witness keeps its keystore under --store"
        await procs.kill("w1")
        after = (await procs.restart("w1")).announced
        assert after["aids"] == before["aids"]
        assert (await client.get(after["oobis"][0])).status == 200
    finally:
        await client.close()
        await procs.stop_all()
        await link.stop()


async def test_two_seeded_pools_with_different_first_indexes_are_different_witnesses(
        tmp_path):
    procs = ProcessController(tmp_path)
    try:
        aids = []
        for index in (0, 1):
            port = free_port()
            announced = (await procs.start(
                f"w{index}", WITNESSES, ("--port", str(port), "--advertise",
                                         f"http://127.0.0.1:{port}", "--seed", "cd" * 14,
                                         "--first", str(index)), timeout=120)).announced
            aids += announced["aids"]
        assert len(set(aids)) == 2
    finally:
        await procs.stop_all()
