"""Actual Linux namespace confinement, bounded streams and process-tree cleanup."""

import asyncio
import os
from pathlib import Path
import shutil
import signal
import tempfile

from agent.protocol import CoreError, strict_loads


class ProcessRunner:
    def __init__(self, root, allowed_programs, *, environment_keys=(), mounts=(), writable=True):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise CoreError("invalid_root", "Process workspace must be a directory")
        self.programs = {str(Path(p).resolve(strict=True)) for p in allowed_programs}
        self.environment_keys = set(environment_keys)
        self.mounts = tuple((Path(p).resolve(strict=True), destination, bool(write)) for p, destination, write in mounts)
        self.writable = writable
        self.bwrap = shutil.which("bwrap")
        self.prlimit = shutil.which("prlimit")
        if not self.bwrap or not self.prlimit:
            raise CoreError("isolation_unavailable", "Linux bubblewrap and prlimit are required")

    async def run(self, argv, *, cwd=".", environment=None, timeout=30, output_limit=1048576,
                  memory_limit=536870912, cpu_seconds=30, cancel=None, stdin=b""):
        if (not isinstance(argv, (list, tuple)) or not argv or any(not isinstance(x, str) or "\x00" in x for x in argv)
                or type(timeout) not in {int, float} or not 0 < timeout <= 3600
                or type(output_limit) is not int or not 1 <= output_limit <= 16777216
                or type(memory_limit) is not int or memory_limit < 16777216
                or type(cpu_seconds) is not int or cpu_seconds < 1
                or not isinstance(stdin, bytes) or len(stdin) > 16777216):
            raise CoreError("validation", "Invalid process arguments or limits", effects="none")
        try:
            program = str(Path(argv[0]).resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise CoreError("program_denied", "Unknown process executable", effects="none") from exc
        if program not in self.programs:
            raise CoreError("program_denied", "Executable is not granted", effects="none")
        path = Path(cwd)
        if path.is_absolute() or ".." in path.parts:
            raise CoreError("path_escape", "Working directory must remain rooted", effects="none")
        candidate = self.root / path
        if not candidate.is_dir() or candidate.resolve() != candidate.absolute():
            raise CoreError("path_escape", "Working directory is missing or follows a symlink", effects="none")
        environment = {} if environment is None else environment
        if (not isinstance(environment, dict) or set(environment) - self.environment_keys
                or any(not isinstance(k, str) or not isinstance(v, str) or "\x00" in k + v or "=" in k
                       for k, v in environment.items())):
            raise CoreError("environment_denied", "Environment is not granted", effects="none")
        if cancel is not None and cancel.is_set():
            raise CoreError("cancelled", "Process cancelled before launch", effects="none")
        command = [self.prlimit, f"--as={memory_limit}", f"--cpu={cpu_seconds}", "--",
                   self.bwrap, "--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
        for system in ("/usr", "/lib", "/lib64", "/bin"):
            if Path(system).exists():
                command.extend(["--ro-bind", system, system])
        command.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                        "--bind" if self.writable else "--ro-bind", str(self.root), "/workspace"])
        for source, destination, write in self.mounts:
            if not isinstance(destination, str) or not destination.startswith("/") or ".." in Path(destination).parts:
                raise CoreError("invalid_mount", "Mount destination must be absolute and bounded", effects="none")
            command.extend(["--bind" if write else "--ro-bind", str(source), destination])
        command.extend(["--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
                        "--setenv", "PYTHONDONTWRITEBYTECODE", "1"])
        for key, value in environment.items():
            command.extend(["--setenv", key, value])
        command.extend(["--chdir", "/workspace" + ("/" + path.as_posix() if str(path) != "." else ""),
                        "--", program, *argv[1:]])
        status_file = tempfile.TemporaryFile()
        command[command.index("--unshare-all"):command.index("--unshare-all")] = ["--json-status-fd", str(status_file.fileno())]
        try:
            process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                        start_new_session=True, pass_fds=(status_file.fileno(),), env={"PATH": "/usr/bin:/bin"})
        except OSError as exc:
            status_file.close()
            raise CoreError("launch_failed", "Cannot start isolated process", effects="none") from exc
        total = 0

        async def read(stream):
            nonlocal total
            chunks = []
            while chunk := await stream.read(65536):
                total += len(chunk)
                if total > output_limit:
                    raise CoreError("output_limit", "Process output exceeded its bound", effects="unknown")
                chunks.append(chunk)
            return b"".join(chunks)

        async def collect():
            stdout = asyncio.create_task(read(process.stdout))
            stderr = asyncio.create_task(read(process.stderr))
            try:
                process.stdin.write(stdin)
                await process.stdin.drain()
                process.stdin.close()
                out, err = await asyncio.gather(stdout, stderr)
                await process.wait()
                status_file.seek(0)
                statuses = [strict_loads(line) for line in status_file.read(65536).splitlines()]
                if not any(type(value.get("child-pid")) is int for value in statuses):
                    raise CoreError("isolation_unconfirmed", "Sandbox did not confirm child startup", effects="unknown")
                terminal = [value["exit-code"] for value in statuses if type(value.get("exit-code")) is int]
                if not terminal:
                    raise CoreError("execution_unconfirmed", "Sandbox did not report a child exit", effects="unknown")
                return {"exit_code": terminal[-1], "launcher_exit_code": process.returncode, "stdout": out, "stderr": err,
                        "isolation": "linux_namespaces", "network": "isolated"}
            finally:
                for task in (stdout, stderr):
                    task.cancel()
                await asyncio.gather(stdout, stderr, return_exceptions=True)

        job = asyncio.create_task(collect())
        cancelled = asyncio.create_task(cancel.wait()) if cancel is not None else None
        try:
            waiters = [job] + ([cancelled] if cancelled else [])
            done, _ = await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if cancelled and cancelled in done:
                raise CoreError("cancelled", "Process cancellation requested", effects="unknown", execution_stopped=True)
            if job not in done:
                raise CoreError("timeout", "Process deadline exceeded", effects="unknown")
            return job.result()
        finally:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
            job.cancel()
            if cancelled:
                cancelled.cancel()
            await asyncio.gather(job, *([cancelled] if cancelled else []), return_exceptions=True)
            status_file.close()
