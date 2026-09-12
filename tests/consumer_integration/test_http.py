"""Actual TCP consumer API to authenticated UNIX worker to real model stream."""
import asyncio
import socket
from contextlib import asynccontextmanager

import httpx
import uvicorn

from consumer.api import create_app
from consumer.config import Settings
from tests.consumer.test_api import TOKEN, PASSWORD, agent, command
from tests.integration.test_continuity import WireModel, proposal


@asynccontextmanager
async def http_installation(tmp_path, model_origin):
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); sock.listen()
    port = sock.getsockname()[1]
    app = create_app(Settings(root=tmp_path, host="127.0.0.1", port=port, bootstrap_token=TOKEN,
                              private_network_origins=(model_origin,)))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False, lifespan="on", proxy_headers=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    async def started():
        while not server.started:
            if task.done(): await task
            await asyncio.sleep(.01)
    await asyncio.wait_for(started(), 5)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=15) as client:
            yield client, app
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


async def test_real_http_worker_model_stream_and_responsive_steering(tmp_path):
    release = asyncio.Event()
    model = WireModel([[proposal("consumer.probe", {})], ["First visible output", release], ["Updated request handled"]])
    wire = await asyncio.start_server(model.handle, "127.0.0.1", 0)
    origin = f"http://127.0.0.1:{wire.sockets[0].getsockname()[1]}"
    try:
        async with http_installation(tmp_path, origin) as (client, app):
            response = await client.post("/api/v1/bootstrap", json={"email": "owner@example.com", "password": PASSWORD, "token": TOKEN})
            assert response.status_code == 200
            client.headers["Authorization"] = "Bearer " + response.json()["data"]["token"]
            owner = await agent(client)
            room = await command(client, owner["id"], "conversations.create", {"title": "Real HTTP"})
            configured = await command(client, owner["id"], "providers.configure", {"provider_id": "wire", "endpoint": origin + "/model", "model": "wire", "context_limit": 64000, "output_limit": 512, "tools": True})
            assert (await command(client, owner["id"], "providers.probe", {"provider_id": "wire", "expected_revision": configured["revision"]}))["ready"]
            await command(client, owner["id"], "providers.activate", {"provider_id": "wire", "expected_revision": configured["revision"]})
            first = await command(client, owner["id"], "messages.send", {"conversation_id": room["id"], "message_id": "slow", "text": "Start slowly"})
            cursor = 0
            async def output():
                nonlocal cursor
                while True:
                    page = (await client.get(f"/api/v1/agents/{owner['id']}/events", params={"after": cursor, "wait": .5})).json()["data"]
                    cursor = page["cursor"]
                    if any(e["payload"].get("content", {}).get("text") == "First visible output" for e in page["items"]): return
            await asyncio.wait_for(output(), 8)
            assert not release.is_set()
            second = await asyncio.wait_for(command(client, owner["id"], "messages.send", {"conversation_id": room["id"], "message_id": "steer", "text": "Change direction", "kind": "steer"}), 1)
            async def finished():
                while True:
                    history = await command(client, owner["id"], "conversations.get", {"id": room["id"]})
                    if len(history["turns"]) == 2 and all(t["state"] in {"completed", "interrupted", "failed"} for t in history["turns"]): return history
                    await asyncio.sleep(.05)
            history = await asyncio.wait_for(finished(), 8)
            states = {t["id"]: t["state"] for t in history["turns"]}
            assert states[first["work_id"]] == "interrupted" and states[second["work_id"]] == "completed"
            assert len(model.requests) == 3
    finally:
        release.set()
        wire.close()
        for task in list(model.tasks): task.cancel()
        await asyncio.gather(*model.tasks, return_exceptions=True)
        await wire.wait_closed()
