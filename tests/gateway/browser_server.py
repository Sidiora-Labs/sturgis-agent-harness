"""Two isolated production applications and a controlled HTTP model for Chromium."""
import asyncio
from contextlib import AsyncExitStack, nullcontext
import json
from pathlib import Path
import signal
import socket
import tempfile

import uvicorn

from consumer.api import create_app as consumer_app
from consumer.config import Settings
from model_gateway.api import create_app as gateway_app
from model_gateway.network import NetworkPolicy
from tests.gateway.test_auth import auth_wire, config
from tests.gateway.test_auth_integration import callback_config, provider_server
from tests.gateway.test_service import server as upstream
from tests.gateway.test_transports import wire


async def main():
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    with tempfile.TemporaryDirectory(prefix="sturgis-gateway-browser-") as directory:
        async with AsyncExitStack() as stack:
            origin, _, requests = await stack.enter_async_context(wire(upstream))
            auth_configs, native, origins = {}, [], [origin]
            for provider in ("github-copilot", "codex", "gemini-cli", "kiro"):
                auth_url, _, _ = await stack.enter_async_context(auth_wire(provider_server(provider)))
                origins.append(auth_url)
                auth_configs[provider] = {**config(auth_url)[provider], "redirect_uri": callback_config(), "authorize_url": auth_url + "/authorize"}
                native.append({"id": provider, "redirect_uri": auth_configs[provider]["redirect_uri"]})
            policy = NetworkPolicy(private_origins=origins, timeout=10)
            sockets, servers, tasks = [], [], []
            for kind in ("consumer", "standalone"):
                sock = socket.socket()
                sock.bind(("127.0.0.1", 0))
                sock.listen()
                sockets.append(sock)
                port = sock.getsockname()[1]
                url = f"http://127.0.0.1:{port}"
                root = Path(directory) / kind
                app = consumer_app(Settings(root=root, host="127.0.0.1", port=port,
                    bootstrap_token="gateway-browser-token", private_network_origins=(origin,))) if kind == "consumer" else gateway_app(
                        root, public_origin=url, network_policy=policy, web_root="consumer/web", auth_configs=auth_configs)
                server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False, proxy_headers=False))
                server.capture_signals = nullcontext
                servers.append(server)
                tasks.append(asyncio.create_task(server.serve(sockets=[sock])))
                while not server.started:
                    if tasks[-1].done():
                        await tasks[-1]
                    await asyncio.sleep(.01)
            # This key belongs solely to the temporary fixture installation.
            print(json.dumps({"url": f"http://127.0.0.1:{sockets[0].getsockname()[1]}",
                "standalone": f"http://127.0.0.1:{sockets[1].getsockname()[1]}",
                "endpoint": origin + "/v1", "admin_key": app.state.auth.key_path.read_text(), "native": native}), flush=True)
            try:
                await stopped.wait()
            finally:
                for server in servers:
                    server.should_exit = True
                await asyncio.gather(*tasks)
                for sock in sockets:
                    sock.close()


if __name__ == "__main__":
    asyncio.run(main())
