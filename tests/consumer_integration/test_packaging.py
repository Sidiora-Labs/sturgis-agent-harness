"""Build/install the actual release wheel, then serve its real private workers."""
import asyncio
import base64
import hashlib
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import zipfile

import httpx
import pytest

from agent.protocol import canonical_json, strict_loads

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def installed_release(tmp_path_factory):
    path = tmp_path_factory.mktemp("installed-release")
    wheels = path / "wheels"
    # Wheel construction and installed-package checks prove release behavior.
    # They must not add temporary/relocated copies to checkout source coverage:
    # pytest-cov starts in pip's build backend before its staging tree is removed.
    release_environment = {key: value for key, value in os.environ.items()
        if not key.startswith(("COV_CORE_", "COVERAGE_"))}
    asset_hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (ROOT / "consumer/web").rglob("*") if p.is_file()}
    result = subprocess.run(["bash", str(ROOT / "packaging/build-wheel.sh"), str(wheels)], cwd=ROOT,
        env={**release_environment, "STURGIS_USE_BUILT_WEB": "1"},
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=240)
    assert result.returncode == 0, result.stdout[-10000:]
    packages = list(wheels.glob("*.whl"))
    assert len(packages) == 1 and not packages[0].name.endswith("none-any.whl")
    wheel = packages[0]
    assert wheel.with_suffix(wheel.suffix + ".sha256").read_text().split()[0] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert "consumer/bin/sturgis-memory" in names
        assert "consumer/bin/LICENSE.MIT" in names and "consumer/bin/vendor-source.md" in names
        assert asset_hashes and set(asset_hashes) <= names
        assert {name: hashlib.sha256(archive.read(name)).hexdigest() for name in asset_hashes} == asset_hashes
        assert any(name.endswith(".woff2") for name in names)
        assert b"[console_scripts]" in archive.read(next(n for n in names if n.endswith("entry_points.txt")))
    target = path / "installed"
    result = subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target), str(wheel)],
        cwd=path, env=release_environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    assert result.returncode == 0, result.stdout
    environment = {**release_environment, "PYTHONPATH": str(target), "PYTHONDONTWRITEBYTECODE": "1"}
    for key in list(environment):
        if key.startswith("STURGIS_"): environment.pop(key)
    # Dependencies use the active interpreter, while every first-party import is
    # resolved from the installed wheel rather than the source checkout.
    location = subprocess.run([sys.executable, "-c",
        "import consumer, coverage; assert coverage.Coverage.current() is None; print(consumer.__file__)"], cwd=path,
        env=environment, text=True, stdout=subprocess.PIPE, check=True)
    assert str(target / "consumer/__init__.py") == location.stdout.strip()
    yield path, target, environment


@pytest.mark.parametrize("mode", ["self_hosted", "hosted"])
async def test_installed_cli_web_native_and_private_worker_start_without_vendor_accounts(installed_release, mode):
    path, installed, environment = installed_release
    root = path / (mode + "-data")
    config = path / (mode + "-client")
    executable = installed / "bin/sturgis"
    setup = await asyncio.create_subprocess_exec(str(executable), "--config-dir", str(config), "--json", "setup", "--root", str(root),
        cwd=path, env=environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    output, error = await asyncio.wait_for(setup.communicate(), 20)
    assert setup.returncode == 0, error.decode()
    configured = strict_loads(output)
    assert configured["native_ready"] and str(installed / "consumer/bin/sturgis-memory") == configured["native_binary"]
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0)); port = reserve.getsockname()[1]
    process = await asyncio.create_subprocess_exec(str(executable), "--config-dir", str(config), "serve", "--root", str(root),
        "--host", "127.0.0.1", "--port", str(port), "--mode", mode, cwd=path, env=environment,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=15) as http:
            deadline = asyncio.get_running_loop().time() + 15
            while True:
                try:
                    health = await http.get("/api/v1/health")
                    if health.status_code == 200: break
                except httpx.TransportError: pass
                assert process.returncode is None and asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(.05)
            page = await http.get("/")
            assert page.status_code == 200 and "Sturgis" in page.text
            paths = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page.text)
            assert paths
            for resource in paths:
                response = await http.get(resource)
                assert response.status_code == 200 and response.content == (installed / "consumer/web" / resource.lstrip("/")).read_bytes()
            token = (root / "bootstrap.token").read_text()
            response = await http.post("/api/v1/bootstrap", content=canonical_json({"email": "owner@example.test",
                "password": "installed-test-password", "token": token}), headers={"Content-Type": "application/json"})
            assert response.status_code == 200, response.text
            owner = response.json()["data"]
            headers = {"Authorization": "Bearer " + owner["token"]}
            agent = (await http.post("/api/v1/agents", json={"name": "Installed agent"}, headers=headers)).json()["data"]
            response = await http.post(f'/api/v1/agents/{agent["id"]}/commands/overview', json={"params": {}, "request_id": "installed_overview"}, headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()["data"]["memory"]["available"]
            status = await http.post(f'/api/v1/agents/{agent["id"]}/lifecycle/status', json={}, headers=headers)
            assert status.status_code == 200, status.text
            assert status.json()["data"]["process"]["isolation"] == ("linux_namespaces" if mode == "hosted" else "trusted_local_process")
            assert (await http.get("/api/v1/agents", headers=headers)).json()["data"]
            raw = b"Content carried through the installed CLI and authenticated HTTP."
            written = await http.post(f'/api/v1/agents/{agent["id"]}/commands/files.write', json={"params": {
                "path": "installed.txt", "data_base64": base64.b64encode(raw).decode()}, "request_id": "installed_file"}, headers=headers)
            assert written.status_code == 200, written.text
            async def cli(*arguments, stdin=None):
                invoked = await asyncio.create_subprocess_exec(str(executable), "--config-dir", str(config),
                    "--url", str(http.base_url).rstrip("/"), "--json", *arguments, cwd=path, env=environment,
                    stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                output, error = await asyncio.wait_for(invoked.communicate(stdin), 30)
                assert invoked.returncode == 0, error.decode()
                return strict_loads(output)
            await cli("login", "--email", "owner@example.test", stdin=b"installed-test-password\n")
            archive_path = path / (mode + ".zip")
            # Wait for the real startup memory sleep to settle before requesting
            # the exclusive snapshot, without relying on an arbitrary delay.
            deadline = asyncio.get_running_loop().time() + 10
            while True:
                state = await http.post(f'/api/v1/agents/{agent["id"]}/lifecycle/suspend', json={}, headers=headers)
                if state.status_code == 200: break
                assert state.json()["error"]["code"] == "runtime_busy", state.text
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(.05)
            exported = await cli("backup", agent["id"], str(archive_path))
            assert archive_path.stat().st_mode & 0o777 == 0o600
            assert exported["sha256"] == hashlib.sha256(archive_path.read_bytes()).hexdigest()
            restored = await cli("restore", str(archive_path), "--name", "Installed restore")
            assert restored["id"] != agent["id"] and restored["owner_id"] == agent["owner_id"]
            response = await http.post(f'/api/v1/agents/{restored["id"]}/commands/files.read', json={
                "params": {"path": "installed.txt"}, "request_id": "installed_read"}, headers=headers)
            assert response.status_code == 200, response.text
            assert base64.b64decode(response.json()["data"]["data_base64"]) == raw
    finally:
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGTERM)
            try: await asyncio.wait_for(process.wait(), 15)
            except TimeoutError:
                os.killpg(process.pid, signal.SIGKILL); await process.wait()
        assert process.returncode in {0, -signal.SIGTERM}, (await process.stderr.read()).decode()[-4000:]
