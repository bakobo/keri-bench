"""The synthetic roles, run as real processes (this.i 6he49n0k)."""

import asyncio
import json
import sys
import time

import httpx
import pytest

from keri_bench.procs import ProcessController

DELAYS = {t: [2.0, 0.1] for t in (
    "constructed>registry_updated", "registry_updated>anchored", "anchored>witnessed",
    "witnessed>published", "revocation_requested>revocation_anchored",
    "revocation_anchored>revocation_witnessed", "revocation_witnessed>revocation_published",
    "delivered>stored", "stored>proof_generated", "proof_generated>verified",
    "sync_full_started>sync_full_done")}
SPEC = {"seed": 7, "clock_skew_ms": 5000.0, "delays": DELAYS, "service_rate": 500.0,
        "stall_after": 1, "stall_ms": 50.0}
SERVER = (sys.executable, "-m", "keri_bench.synthetic.server")


@pytest.fixture
async def stack(tmp_path):
    procs = ProcessController(tmp_path)
    truth = tmp_path / "truth.jsonl"
    urls = {}
    for role in ("state_distribution", "issuer", "holder", "verifier"):
        args = (role, "--spec", json.dumps(SPEC), "--truth", str(truth))
        if role in ("issuer", "verifier"):
            args += ("--state", urls["state_distribution"])
        urls[role] = (await procs.start(role, SERVER, args)).announced["url"]
    yield urls, truth
    await procs.stop_all()


async def test_a_credential_goes_through_issue_publish_and_revoke(stack):
    urls, truth = stack
    async with httpx.AsyncClient(timeout=10) as client:
        issuer, verifier = urls["issuer"], urls["verifier"]
        assert (await client.post(f"{issuer}/issue/c1")).status_code == 202
        assert (await client.post(f"{issuer}/issue/c1")).status_code == 202  # idempotent
        await client.post(f"{issuer}/issue/c2")
        op = (await client.get(f"{issuer}/operations/c2?until=witnessed")).json()
        assert set(op["milestones"]) == {"constructed", "registry_updated", "anchored",
                                         "witnessed"}
        assert op["said"] == "Ec2"
        clock = (await client.get(f"{issuer}/clock")).json()["t"]
        assert abs(clock - (time.time_ns() + 5_000_000_000)) < 1_000_000_000
        assert (await client.get(f"{issuer}/anchors/c2")).json() == {"anchors": 1}
        assert (await client.get(f"{issuer}/anchors/zz")).json() == {"anchors": 0}
        for _ in range(100):
            status = (await client.get(f"{verifier}/status/c2")).json()
            if status["status"] == "valid":
                break
            await asyncio.sleep(0.01)
        assert status == {"status": "valid", "said": "Ec2"}
        assert (await client.post(f"{issuer}/revoke/c2")).status_code == 202
        await client.get(f"{issuer}/operations/c2?until=revocation_witnessed")
        for _ in range(100):
            if (await client.get(f"{verifier}/status/c2")).json()["status"] == "revoked":
                break
            await asyncio.sleep(0.01)
        assert (await client.get(f"{verifier}/status/c2")).json()["status"] == "revoked"
        assert (await client.get(f"{verifier}/status/nope")).json() == {
            "status": "unknown", "said": None}
        assert (await client.post(f"{issuer}/revoke/nope")).status_code == 404
        assert (await client.get(f"{issuer}/operations/nope")).status_code == 404
        assert (await client.get(f"{issuer}/nowhere")).status_code == 404
    lines = [json.loads(line) for line in truth.read_text().splitlines()]
    c2 = {(line["start"], line["end"]) for line in lines if line["credential"] == "c2"}
    assert ("anchored", "witnessed") in c2 and ("revocation_witnessed",
                                                "revocation_published") in c2
    assert all(line["elapsed_ns"] >= line["floor_ns"] >= line["injected_ns"] > 0
               for line in lines)


async def test_the_issuer_stalls_once_after_the_configured_count(stack):
    urls, _ = stack
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(f"{urls['issuer']}/issue/a")
        await client.get(f"{urls['issuer']}/operations/a?until=constructed")
        began = time.monotonic()
        await client.post(f"{urls['issuer']}/issue/b")
        await client.get(f"{urls['issuer']}/operations/b?until=constructed")
        assert time.monotonic() - began >= 0.05


async def test_the_holder_and_verifier_answer_with_their_injected_delays(stack):
    urls, truth = stack
    async with httpx.AsyncClient(timeout=10) as client:
        holder, verifier = urls["holder"], urls["verifier"]
        assert (await client.post(f"{holder}/deliver/c1", content=b"grant")).status_code == 202
        assert (await client.post(f"{holder}/admit/c1")).status_code == 200
        assert (await client.post(f"{holder}/present/c1")).json() == {"presentation": "Pc1"}
        assert (await client.post(f"{verifier}/verify/c1")).json() == {"valid": True}
        assert (await client.post(f"{verifier}/sync/run1")).status_code == 200
    ends = {json.loads(line)["end"] for line in truth.read_text().splitlines()}
    assert {"stored", "proof_generated", "verified", "sync_full_done"} <= ends
