"""Tests for the proxy endpoint (/proxy-ws).

The proxy runs in-process under uvicorn, and a fake upstream server stands in for
/client-ws, so these tests need only fastapi, uvicorn, aiohttp, websockets, loguru
and pytest-asyncio, not the full Open-LLM-VTuber dependencies.

    uv run --with pytest --with pytest-asyncio pytest tests/test_proxy_handler.py
"""

import asyncio
import json
import socket

import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI, WebSocket
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from src.open_llm_vtuber.proxy_handler import ProxyHandler

CHAIN_START = {"type": "control", "text": "conversation-chain-start"}
CHAIN_END = {"type": "control", "text": "conversation-chain-end"}


class FakeServer:
    """Stands in for /client-ws: records what the proxy forwards to the server."""

    def __init__(self):
        self.received = []
        self.connections = set()
        self.port = None
        self._server = None

    async def start(self):
        self._server = await serve(self._handle, "127.0.0.1", 0)
        self.port = next(iter(self._server.sockets)).getsockname()[1]

    async def stop(self):
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, websocket):
        self.connections.add(websocket)
        try:
            async for raw in websocket:
                self.received.append(json.loads(raw))
        except Exception:
            pass
        finally:
            self.connections.discard(websocket)

    async def broadcast(self, message):
        for websocket in list(self.connections):
            await websocket.send(json.dumps(message))

    def types(self):
        return [message.get("type") for message in self.received]


async def wait_until(condition, timeout=5.0):
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


async def receive_until(websocket, expected, timeout=5.0):
    async with asyncio.timeout(timeout):
        while True:
            message = json.loads(await websocket.recv())
            if message == expected:
                return


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest_asyncio.fixture
async def proxy():
    upstream = FakeServer()
    await upstream.start()
    handler = ProxyHandler(f"ws://127.0.0.1:{upstream.port}/client-ws")
    app = FastAPI()

    # Same as routes.init_proxy_route, without importing the whole server.
    @app.websocket("/proxy-ws")
    async def proxy_endpoint(websocket: WebSocket):
        await handler.handle_client_connection(websocket)

    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    await wait_until(lambda: server.started)
    yield upstream, f"ws://127.0.0.1:{port}/proxy-ws"
    server.should_exit = True
    await task
    await handler.disconnect()
    await upstream.stop()


@pytest.mark.asyncio
async def test_text_input_is_forwarded_when_no_conversation_is_active(proxy):
    upstream, url = proxy
    async with connect(url) as frontend, connect(url) as agent:
        await wait_until(lambda: upstream.types().count("request-init-config") == 2)
        await agent.send(json.dumps({"type": "text-input", "text": "hello"}))
        await wait_until(lambda: "text-input" in upstream.types())
        assert upstream.types()[-2:] == ["user-input-transcription", "text-input"]
        # The other client sees the input as a transcription.
        await receive_until(
            frontend, {"type": "user-input-transcription", "text": "hello"}
        )


@pytest.mark.asyncio
async def test_queued_text_input_waits_for_a_conversation_started_outside_the_queue(
    proxy,
):
    upstream, url = proxy
    async with connect(url) as frontend, connect(url) as agent:
        await wait_until(lambda: upstream.connections)
        # A voice turn starts. mic-audio-end is forwarded directly, not through the queue.
        await upstream.broadcast(CHAIN_START)
        await receive_until(frontend, CHAIN_START)
        await agent.send(json.dumps({"type": "text-input", "text": "report"}))
        await asyncio.sleep(0.5)
        assert "text-input" not in upstream.types()

        await upstream.broadcast(CHAIN_END)
        await wait_until(lambda: "text-input" in upstream.types())


@pytest.mark.asyncio
async def test_init_config_false_skips_the_init_config_request(proxy):
    upstream, url = proxy
    async with connect(url + "?init_config=false"):
        await wait_until(lambda: upstream.connections)
        await asyncio.sleep(0.3)
        assert "request-init-config" not in upstream.types()
        async with connect(url):
            await wait_until(lambda: "request-init-config" in upstream.types())
    assert upstream.types().count("request-init-config") == 1
