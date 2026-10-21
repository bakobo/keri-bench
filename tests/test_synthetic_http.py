"""The synthetic roles' minimal HTTP server."""

import asyncio
import json

import httpx

from keri_bench.synthetic import http


async def handler(method, path, body):
    if path == "/boom":
        raise RuntimeError("synthetic bug")
    return 200, {"method": method, "path": path, "body": body.decode()}


async def raw(port, data):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(data)
    await writer.drain()
    writer.write_eof()
    answer = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    return answer


async def test_it_answers_json_over_keep_alive_and_reports_handler_bugs():
    server = await http.serve(handler, 0)
    port = server.sockets[0].getsockname()[1]
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
        first = await client.post("/a", content=b"hi")
        second = await client.get("/b")
        boom = await client.get("/boom")
    assert first.json() == {"method": "POST", "path": "/a", "body": "hi"}
    assert second.json()["path"] == "/b"
    assert boom.status_code == 500 and "synthetic bug" in boom.json()["error"]
    server.close()
    await server.wait_closed()


async def test_it_refuses_malformed_and_oversized_requests(monkeypatch):
    monkeypatch.setattr(http, "MAX_BODY", 4)
    server = await http.serve(handler, 0)
    port = server.sockets[0].getsockname()[1]
    bad = await raw(port, b"NONSENSE\r\n\r\n")
    big = await raw(port, b"POST /a HTTP/1.1\r\nContent-Length: 10\r\n\r\n0123456789")
    odd = await raw(port, b"POST /a HTTP/1.1\r\nContent-Length: x\r\n\r\n")
    assert bad.startswith(b"HTTP/1.1 400") and odd.startswith(b"HTTP/1.1 400")
    assert big.startswith(b"HTTP/1.1 413")
    assert json.loads(big.split(b"\r\n\r\n", 1)[1])["error"]
    server.close()
    await server.wait_closed()


async def test_a_client_that_hangs_up_mid_body_is_dropped_quietly():
    server = await http.serve(handler, 0)
    port = server.sockets[0].getsockname()[1]
    assert await raw(port, b"POST /a HTTP/1.1\r\nContent-Length: 10\r\n\r\n01") == b""
    server.close()
    await server.wait_closed()


async def test_a_server_may_send_fixed_headers_with_every_answer():
    from keri_bench.http import HttpClient
    from keri_bench.synthetic import http as shttp

    async def ok(method, path, body):
        return 200, {}

    server = await shttp.serve(ok, 0, headers={"Signature": "sig1=:x:"})
    client = HttpClient()
    try:
        answer = await client.get(f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/")
        assert answer.headers["Signature"] == "sig1=:x:"
    finally:
        await client.close()
        server.close()
        await server.wait_closed()
