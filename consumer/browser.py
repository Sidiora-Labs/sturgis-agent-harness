"""Managed Chromium: isolated network namespace, bounded CDP pipe, mediated HTTP."""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
import os
from pathlib import Path
import resource
import shutil
import signal
import sys

from agent.protocol import CoreError, canonical_json, strict_loads
from consumer.network import origin

_LAUNCH = """import os,sys,fcntl
r=fcntl.fcntl(int(sys.argv[1]),fcntl.F_DUPFD_CLOEXEC,10)
w=fcntl.fcntl(int(sys.argv[2]),fcntl.F_DUPFD_CLOEXEC,10)
os.dup2(r,3,inheritable=True);os.dup2(w,4,inheritable=True)
os.execv(sys.argv[3],sys.argv[3:])
"""


class CDPPipe:
    def __init__(self, reader, writer):
        self.reader, self.writer, self.next_id = reader, writer, 0
        self.pending, self.events = {}, asyncio.Queue(maxsize=512)
        self.receiver = asyncio.create_task(self._receive())

    async def _receive(self):
        try:
            while True:
                raw = await self.reader.readuntil(b"\0")
                value = strict_loads(raw[:-1])
                if not isinstance(value, dict):
                    raise CoreError("invalid_cdp", "Browser returned a malformed CDP frame")
                if "id" in value:
                    future = self.pending.pop(value["id"], None)
                    if future and not future.done():
                        if "error" in value:
                            future.set_exception(CoreError("browser_command", "Browser rejected the CDP command"))
                        else:
                            future.set_result(value.get("result", {}))
                else:
                    self.events.put_nowait(value)
        except asyncio.CancelledError:
            pass
        except Exception:
            for future in self.pending.values():
                if not future.done(): future.set_exception(CoreError("browser_unavailable", "Browser CDP channel closed or exceeded its frame bound"))
            self.pending.clear()

    async def call(self, method, params=None, *, session=None):
        self.next_id += 1
        message = {"id": self.next_id, "method": method, "params": params or {}}
        if session: message["sessionId"] = session
        encoded = canonical_json(message).encode() + b"\0"
        if len(encoded) > 12582912:
            raise CoreError("browser_limit", "CDP command exceeded its byte bound")
        future = asyncio.get_running_loop().create_future()
        self.pending[self.next_id] = future
        self.writer.write(encoded)
        await self.writer.drain()
        return await future

    async def close(self):
        self.receiver.cancel()
        await asyncio.gather(self.receiver, return_exceptions=True)
        self.writer.close()


class BrowserBackend:
    def __init__(self, binary, *, timeout=20, max_requests=64, max_total_bytes=16777216, max_resident_bytes=2147483648):
        self.binary = Path(binary).resolve()
        self.bwrap, self.prlimit = shutil.which("bwrap"), shutil.which("prlimit")
        if (not self.binary.is_file() or not os.access(self.binary, os.X_OK) or not self.bwrap or not self.prlimit
                or type(timeout) not in {int, float} or not 0 < timeout <= 120
                or type(max_requests) is not int or not 1 <= max_requests <= 256
                or type(max_total_bytes) is not int or not 1024 <= max_total_bytes <= 67108864
                or type(max_resident_bytes) is not int or not 16777216 <= max_resident_bytes <= 2147483648):
            raise CoreError("browser_unavailable", "An executable Chromium, Linux isolation and bounded browser limits are required")
        self.timeout, self.max_requests, self.max_total_bytes = timeout, max_requests, max_total_bytes
        self.max_resident_bytes = max_resident_bytes

    @staticmethod
    def _resident_tree(pid):
        pending, seen, total = [pid], set(), 0
        while pending:
            current = pending.pop()
            if current in seen: continue
            seen.add(current)
            try:
                total += int(Path(f"/proc/{current}/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
                pending.extend(int(child) for child in Path(f"/proc/{current}/task/{current}/children").read_text().split())
            except (FileNotFoundError, ProcessLookupError):
                pass
        return total

    @staticmethod
    def _failure(stderr, fallback):
        if b"loopback: Failed RTM_NEWADDR" in stderr or b"Creating new namespace failed" in stderr:
            return CoreError("browser_unavailable", "Operator process policy denied the isolated browser network namespace", reason="network_namespace_denied", effects="none")
        if b"CagedHeap reservation" in stderr or b"pointer compression cage" in stderr:
            return CoreError("browser_unavailable", "Operator address-space ceiling prevented browser renderer startup", reason="renderer_address_space_limit", effects="none")
        return fallback

    @asynccontextmanager
    async def _session(self):
        read_child, write_parent = os.pipe()
        read_parent, write_child = os.pipe()
        command = [self.bwrap, "--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
        for system in ("/usr", "/lib", "/lib64", "/bin"):
            if Path(system).exists(): command.extend(["--ro-bind", system, system])
        if Path("/etc/fonts").exists(): command.extend(["--ro-bind", "/etc/fonts", "/etc/fonts"])
        command.extend(["--ro-bind", str(self.binary.parent), "/browser", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--tmpfs", "/run", "--dir", "/home", "--setenv", "HOME", "/home", "--setenv", "PATH", "/usr/bin:/bin",
            "--", self.prlimit, "--cpu=" + str(max(1, int(self.timeout))), "--nofile=256", "--",
            "/browser/" + self.binary.name, "--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--no-first-run",
            "--no-default-browser-check", "--disable-background-networking", "--disable-component-update", "--disable-extensions",
            "--disable-sync", "--disable-gpu", "--disable-breakpad", "--disable-crash-reporter", "--metrics-recording-only",
            "--js-flags=--max-old-space-size=128", "--user-data-dir=/tmp/profile", "--remote-debugging-pipe", "about:blank"])
        process = None
        monitor = None
        logger = None
        stderr = bytearray()
        transports = []
        limits = [274877906944, *(limit for limit in resource.getrlimit(resource.RLIMIT_AS) if limit != resource.RLIM_INFINITY)]
        try:
            command.insert(command.index("--nofile=256"), "--as=" + str(min(limits)))
            process = await asyncio.create_subprocess_exec(sys.executable, "-c", _LAUNCH, str(read_child), str(write_child), *command,
                pass_fds=(read_child, write_child), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE, start_new_session=True, env={"PATH": "/usr/bin:/bin"})
            async def read_log():
                while chunk := await process.stderr.read(4096):
                    stderr.extend(chunk[:max(0, 65536 - len(stderr))])
            logger = asyncio.create_task(read_log())
            os.close(read_child); os.close(write_child)
            read_child = write_child = -1
            loop = asyncio.get_running_loop()
            reader = asyncio.StreamReader(limit=12582912)
            transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(read_parent, "rb", buffering=0))
            transports.append(transport); read_parent = -1
            transport, protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, os.fdopen(write_parent, "wb", buffering=0))
            transports.append(transport); write_parent = -1
            writer = asyncio.StreamWriter(transport, protocol, None, loop)
            pipe = CDPPipe(reader, writer)
            async def memory_watch():
                while process.returncode is None:
                    if await asyncio.to_thread(self._resident_tree, process.pid) > self.max_resident_bytes:
                        os.killpg(process.pid, signal.SIGKILL)
                        raise CoreError("browser_memory_limit", "Browser process tree exceeded its sampled resident memory budget", effects="none")
                    await asyncio.sleep(.05)
            monitor = asyncio.create_task(memory_watch())
            try:
                async with asyncio.timeout(self.timeout):
                    version = await pipe.call("Browser.getVersion")
                    if not isinstance(version.get("product"), str):
                        raise CoreError("browser_unavailable", "Browser did not return its actual version")
                    yield pipe, version
            except TimeoutError:
                raise self._failure(stderr, CoreError("timeout", "Isolated browser exceeded its deadline", effects="none")) from None
            except CoreError as exc:
                if monitor.done() and not monitor.cancelled() and monitor.exception():
                    raise monitor.exception()
                raise self._failure(stderr, exc) from None
            finally:
                await pipe.close()
        except OSError:
            raise CoreError("browser_unavailable", "Contained Chromium could not be launched", effects="none") from None
        finally:
            if monitor is not None:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
            if process is not None:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                await process.wait()
            if logger is not None:
                await logger
            for transport in transports: transport.close()
            for fd in (read_child, write_child, read_parent, write_parent):
                if fd >= 0: os.close(fd)

    async def probe(self):
        async with self._session() as (pipe, version):
            target = (await pipe.call("Target.createTarget", {"url": "about:blank"}))["targetId"]
            session = (await pipe.call("Target.attachToTarget", {"targetId": target, "flatten": True}))["sessionId"]
            value = await pipe.call("Runtime.evaluate", {"expression": "1+2", "returnByValue": True}, session=session)
            if value.get("result", {}).get("value") != 3:
                raise CoreError("browser_unavailable", "Browser renderer could not evaluate its readiness check")
            return {"ok": True, "kind": "isolated_chromium", "product": version["product"], "network": "namespace_isolated"}

    async def inspect(self, url, *, network, allowed_origins, artifact_store, scope):
        if origin(url) not in {origin(item) for item in allowed_origins} or not url.startswith(("http://", "https://")):
            raise CoreError("network_origin_denied", "Browser document origin is not granted", effects="none")
        async with self._session() as (pipe, version):
            context = (await pipe.call("Target.createBrowserContext"))["browserContextId"]
            await pipe.call("Browser.setDownloadBehavior", {"behavior": "deny", "browserContextId": context})
            target = (await pipe.call("Target.createTarget", {"url": "about:blank", "browserContextId": context}))["targetId"]
            session = (await pipe.call("Target.attachToTarget", {"targetId": target, "flatten": True}))["sessionId"]
            await pipe.call("Page.enable", session=session)
            await pipe.call("Network.enable", session=session)
            await pipe.call("Network.setBypassServiceWorker", {"bypass": True}, session=session)
            await pipe.call("Network.setBlockedURLs", {"urls": ["file://*", "ftp://*", "ws://*", "wss://*"]}, session=session)
            await pipe.call("Page.addScriptToEvaluateOnNewDocument", {"source":
                "for(const k of ['Worker','SharedWorker','WebSocket','RTCPeerConnection']) Object.defineProperty(globalThis,k,{value:undefined,configurable:false});"
                "if(navigator.serviceWorker) Object.defineProperty(navigator.serviceWorker,'register',{value:()=>Promise.reject(new Error('Unsupported')),configurable:false});"
                "Object.defineProperty(globalThis,'open',{value:()=>null,configurable:false});"}, session=session)
            await pipe.call("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 720, "deviceScaleFactor": 1, "mobile": False}, session=session)
            await pipe.call("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]}, session=session)
            observed, blocked = [], []
            total = 0
            loaded = asyncio.Event()

            async def events():
                nonlocal total
                while True:
                    event = await pipe.events.get()
                    if event.get("sessionId") != session: continue
                    if event.get("method") == "Page.loadEventFired": loaded.set()
                    if event.get("method") != "Fetch.requestPaused": continue
                    params = event["params"]
                    request = params["request"]
                    try:
                        if len(observed) + len(blocked) >= self.max_requests:
                            raise CoreError("browser_limit", "Browser request count exceeded its bound")
                        if request["method"] not in {"GET", "HEAD"}:
                            raise CoreError("browser_method_denied", "Browser inspection permits only GET and HEAD")
                        response = await network.request(request["method"], request["url"], allowed_origins=allowed_origins,
                            max_bytes=min(network.max_bytes, self.max_total_bytes - total), follow_redirects=False)
                        total += len(response.body)
                        headers = [{"name": key, "value": value} for key, value in response.headers.items()
                            if key.lower() not in {"content-length", "transfer-encoding", "connection", "content-encoding", "set-cookie"}]
                        await pipe.call("Fetch.fulfillRequest", {"requestId": params["requestId"], "responseCode": response.status,
                            "responseHeaders": headers, "body": base64.b64encode(response.body).decode()}, session=session)
                        observed.append({"url": response.url, "status": response.status, "bytes": len(response.body)})
                    except CoreError as exc:
                        blocked.append({"url": request["url"][:8192], "code": exc.code})
                        await pipe.call("Fetch.failRequest", {"requestId": params["requestId"], "errorReason": "BlockedByClient"}, session=session)
            mediator = asyncio.create_task(events())
            load_task = asyncio.create_task(loaded.wait())
            try:
                navigation = await pipe.call("Page.navigate", {"url": url}, session=session)
                if navigation.get("errorText"):
                    raise CoreError("browser_navigation", "Browser could not navigate to the granted document", effects="none")
                done, _ = await asyncio.wait([mediator, load_task], return_when=asyncio.FIRST_COMPLETED)
                if mediator in done: mediator.result()
                result = await pipe.call("Runtime.evaluate", {"expression": "({title:document.title,text:document.body?.innerText.slice(0,65536)||'',url:location.href})", "returnByValue": True}, session=session)
                value = result.get("result", {}).get("value")
                if not isinstance(value, dict): raise CoreError("browser_result", "Browser could not return rendered document text")
                screenshot = await pipe.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}, session=session)
                data = base64.b64decode(screenshot["data"], validate=True)
                artifact = await asyncio.to_thread(artifact_store.put, scope, data, media_type="image/png")
                return {**value, "screenshot_artifact_id": artifact.id, "requests": observed, "blocked": blocked,
                    "browser": version["product"], "network": "namespace_isolated", "trust": "external_evidence"}
            finally:
                mediator.cancel()
                load_task.cancel()
                await asyncio.gather(mediator, load_task, return_exceptions=True)
