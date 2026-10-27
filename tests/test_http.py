"""The adapters' shared HTTP client."""

import asyncio

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


@pytest.mark.parametrize("framing", ["length", "chunked"])
async def test_an_answer_that_arrives_in_pieces_is_read_whole(framing):
    # A body that reaches the client in two writes, the second after a pause, as imbu's larger
    # operation documents did: a read that takes what has arrived returns only the first half.
    body = b'{"said": "' + b"E" * 3000 + b'"}'
    half = len(body) // 2

    async def serve(reader, writer):
        while (await reader.readline()) not in (b"\r\n", b""):
            pass
        if framing == "length":
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(body))
            parts = [body[:half], body[half:]]
        else:
            writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
            parts = [b"%x\r\n%s\r\n" % (half, body[:half]),
                     b"%x\r\n%s\r\n0\r\n\r\n" % (len(body) - half, body[half:])]
        for part in parts:
            writer.write(part)
            await writer.drain()
            await asyncio.sleep(0.05)
        writer.close()

    listening = await asyncio.start_server(serve, "127.0.0.1", 0)
    client = http.HttpClient(timeout_s=5)
    got = await client.get(f"http://127.0.0.1:{listening.sockets[0].getsockname()[1]}/op")
    assert got.body == body and got.json()["said"] == "E" * 3000
    await client.close()
    listening.close()
    await listening.wait_closed()
