"""The adapters' shared HTTP client."""

import pytest

from keri_bench import http
from keri_bench.synthetic import http as server


async def answer(method, path, body):
    return 200, {"path": path, "size": len(body)}


async def test_it_round_trips_and_refuses_an_oversized_answer(monkeypatch):
    listening = await server.serve(answer, 0)
    url = f"http://127.0.0.1:{listening.sockets[0].getsockname()[1]}"
    client = http.HttpClient(timeout_s=5)
    got = await client.post(f"{url}/x", body=b"abc")
    assert got.status == 200 and got.json() == {"path": "/x", "size": 3}
    assert got.headers["Content-Type"] == "application/json"
    assert (await client.get(f"{url}/y")).json()["path"] == "/y"
    monkeypatch.setattr(http, "MAX_ANSWER", 4)
    with pytest.raises(ValueError):
        await client.get(f"{url}/z")
    await client.close()
    await client.close()  # closing twice is harmless
    listening.close()
    await listening.wait_closed()
