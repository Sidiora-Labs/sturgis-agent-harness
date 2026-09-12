"""Immutable operator configuration; clients cannot choose roots or tenancy."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

from agent.protocol import CoreError


@dataclass(frozen=True, slots=True)
class Settings:
    root: Path = field(default_factory=lambda: Path.home() / ".local/share/sturgis")
    mode: str = "self_hosted"
    host: str = "127.0.0.1"
    port: int = 8787
    native_binary: Path | None = None
    source_root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[1])
    worker_timeout: float = 30.0
    bootstrap_token: str = field(default="", repr=False)
    allow_signup: bool = False
    private_network_origins: tuple[str, ...] = ()
    browser_binary: Path | None = None
    worker_memory_bytes: int = 2_147_483_648
    public_origin: str | None = None

    def __post_init__(self):
        if self.mode not in {"self_hosted", "hosted"}:
            raise CoreError("invalid_config", "Unknown deployment mode")
        if not isinstance(self.host, str) or not self.host or any(c in self.host for c in " /\\\r\n"):
            raise CoreError("invalid_config", "Invalid listen host")
        if type(self.port) is not int or not 0 <= self.port <= 65535:
            raise CoreError("invalid_config", "Invalid listen port")
        if isinstance(self.worker_timeout, bool) or not isinstance(self.worker_timeout, (int, float)) or not math.isfinite(self.worker_timeout) or not 0.1 <= self.worker_timeout <= 300:
            raise CoreError("invalid_config", "Worker timeout must be between 0.1 and 300 seconds")
        if type(self.allow_signup) is not bool or not isinstance(self.bootstrap_token, str):
            raise CoreError("invalid_config", "Invalid account configuration")
        if type(self.worker_memory_bytes) is not int or not 134_217_728 <= self.worker_memory_bytes <= 274_877_906_944:
            raise CoreError("invalid_config", "Worker address-space limit must be between 128 MiB and 256 GiB")
        if self.public_origin is not None:
            from consumer.network import exact_origin
            public = exact_origin(self.public_origin)
            if not public.startswith(("http://", "https://")):
                raise CoreError("invalid_config", "Public origin must use HTTP or HTTPS")
            object.__setattr__(self, "public_origin", public)
        for name in ("root", "source_root"):
            object.__setattr__(self, name, Path(getattr(self, name)).expanduser().resolve())
        packaged = Path(__file__).parent / "bin/sturgis-memory"
        native = self.native_binary or (packaged if packaged.is_file() else self.source_root / ".build/native/sturgis-memory")
        object.__setattr__(self, "native_binary", Path(native).expanduser().resolve())
        if self.browser_binary is not None:
            object.__setattr__(self, "browser_binary", Path(self.browser_binary).expanduser().resolve())
        origins = []
        for origin in self.private_network_origins:
            try:
                parsed = urlsplit(origin)
                valid = parsed.scheme in {"http", "https", "ws", "wss"} and parsed.hostname and not parsed.username and not parsed.password and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
                _ = parsed.port
            except (TypeError, ValueError):
                valid = False
            if not valid:
                raise CoreError("invalid_config", "Private network exceptions must be exact origins")
            origins.append(origin.rstrip("/"))
        object.__setattr__(self, "private_network_origins", tuple(sorted(set(origins))))

    @classmethod
    def from_env(cls, environ=None):
        env = os.environ if environ is None else environ
        options = {}
        for key in ("root", "mode", "host", "native_binary", "source_root", "bootstrap_token", "browser_binary", "public_origin"):
            if "STURGIS_" + key.upper() in env:
                options[key] = env["STURGIS_" + key.upper()]
        try:
            if "STURGIS_PORT" in env:
                options["port"] = int(env["STURGIS_PORT"])
            if "STURGIS_WORKER_TIMEOUT" in env:
                options["worker_timeout"] = float(env["STURGIS_WORKER_TIMEOUT"])
            if "STURGIS_WORKER_MEMORY_BYTES" in env:
                options["worker_memory_bytes"] = int(env["STURGIS_WORKER_MEMORY_BYTES"])
        except ValueError:
            raise CoreError("invalid_config", "Invalid numeric operator configuration") from None
        signup = env.get("STURGIS_ALLOW_SIGNUP", "false")
        if signup not in {"true", "false"}:
            raise CoreError("invalid_config", "STURGIS_ALLOW_SIGNUP must be true or false")
        options["allow_signup"] = signup == "true"
        options["private_network_origins"] = tuple(filter(None, env.get("STURGIS_PRIVATE_NETWORK_ORIGINS", "").split(",")))
        return cls(**options)
