"""Preset isolation, actual endpoint conventions and malformed URL boundaries."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from model_gateway.catalog import PROTOCOLS, normalize_endpoint, provider_preset, provider_presets
from model_gateway.errors import GatewayError


def test_catalog_is_serializable_and_mutations_cannot_reconfigure_other_clients():
    first = provider_presets()
    assert len(first) >= 30 and len({row["id"] for row in first}) == len(first)
    assert json.loads(json.dumps(first)) == first
    first[0]["defaults"]["tools"] = True
    first[0]["auth_modes"].append("fake")
    first[0]["endpoint"] = "https://attacker.test"
    original = provider_preset(first[0]["id"])
    assert original["defaults"]["tools"] is False
    assert "fake" not in original["auth_modes"]
    assert original["endpoint"] != first[0]["endpoint"]
    original["defaults"]["context_limit"] = 1
    assert provider_preset(original["id"])["defaults"]["context_limit"] == 8192
    assert provider_preset("missing") is None
    assert provider_preset(None) is None


@pytest.mark.parametrize("row", provider_presets(), ids=lambda row: row["id"])
def test_each_preset_is_an_executable_protocol_or_custom_endpoint(row):
    assert row["protocol"] in PROTOCOLS
    assert row["auth"] in {"none", "api_key"}
    assert row["auth"] in row["auth_modes"]
    assert row["category"] in {"free_tier", "paid", "gateway", "local", "custom"}
    assert row["description"] and row["name"]
    limits = row["defaults"]
    assert 0 < limits["output_limit"] < limits["context_limit"] <= 2_000_000
    assert type(limits["tools"]) is bool
    if row["category"] == "custom":
        assert row["endpoint"] == ""
    else:
        assert normalize_endpoint(row["endpoint"], row["protocol"]) == row["endpoint"]
    assert row["key_url"] == "" or row["key_url"].startswith("https://")


def test_public_local_and_external_accounts_are_not_misrepresented():
    assert provider_preset("opencode")["auth"] == "none"
    assert provider_preset("opencode")["model_filter"] == "opencode_free"
    for provider_id in ("ollama", "lmstudio", "vllm", "llama-cpp", "9router"):
        row = provider_preset(provider_id)
        assert row["auth"] == "none" and "api_key" in row["auth_modes"]
    bridge = provider_preset("9router")
    assert bridge["integration_status"] == "external_gateway"
    assert bridge["preserves_model_namespace"] is True
    assert all("oauth" not in row["auth_modes"] for row in provider_presets())
    assert provider_preset("github-models") is None  # Official service retired July 2026.
    assert provider_preset("mimo-free") is None  # Requires its separate bootstrap/executor.


@pytest.mark.parametrize(("endpoint", "protocol", "expected"), [
    ("  HTTPS://API.Example.test/v1///  ", "openai", "https://api.example.test/v1"),
    ("https://api.example.test/v1/chat/completions", "openai", "https://api.example.test/v1"),
    ("https://api.example.test/proxy/api/v4/models", "openai", "https://api.example.test/proxy/api/v4"),
    ("https://api.example.test/v1/messages", "anthropic", "https://api.example.test/v1"),
    ("https://api.example.test/v1/models/", "anthropic", "https://api.example.test/v1"),
    ("https://api.example.test/v1/responses", "openai-responses", "https://api.example.test/v1"),
    ("https://api.example.test/v1/models", "openai-responses", "https://api.example.test/v1"),
    ("https://api.example.test/v1beta/models", "gemini", "https://api.example.test/v1beta"),
    ("https://api.example.test/v1beta/models/gemini-test:generateContent", "gemini", "https://api.example.test/v1beta"),
    ("https://api.example.test/v1beta/models/gemini-test:streamGenerateContent", "gemini", "https://api.example.test/v1beta"),
    ("https://api.perplexity.ai/", "openai", "https://api.perplexity.ai"),
    ("https://api.example.test/chat/completions", "openai", "https://api.example.test"),
    ("http://127.0.0.1:11434/v1", "openai", "http://127.0.0.1:11434/v1"),
    ("http://[0:0:0:0:0:0:0:1]:11434/v1", "openai", "http://[::1]:11434/v1"),
    ("http://[::1]/v1", "openai", "http://[::1]/v1"),
    ("https://bücher.example/v1", "openai", "https://xn--bcher-kva.example/v1"),
    ("https://api.example.test/custom/messages-other", "anthropic", "https://api.example.test/custom/messages-other"),
    ("https://api.example.test/v1/responses", "openai", "https://api.example.test/v1/responses"),
])
def test_endpoint_normalization_preserves_real_provider_and_proxy_paths(endpoint, protocol, expected):
    assert normalize_endpoint(endpoint, protocol) == expected
    assert normalize_endpoint(expected, protocol) == expected


@pytest.mark.parametrize("endpoint", [
    None, 12, "", "  ", "x" * 2049, "api.example.test/v1", "ftp://example.test/v1",
    "https:///v1", "https://user:secret@example.test/v1", "https://user@example.test/v1",
    "https://api.example.test/v1?key=secret", "https://api.example.test/v1#secret",
    "https://api.example.test/v1?", "https://api.example.test/v1#", "https://bad host/v1",
    "https://api.example.test/v1\n/chat", "https://api.example.test\x7f/v1",
    "https://api.example.test\\@other.test/v1", "https://api.example.test:99999/v1",
    "https://api.example.test:0/v1", "https://api.example.test:no/v1",
    "http://[bad-ip]/v1", "http://[::1/v1", "https://%65xample.test/v1",
    "https://./v1", "https://\ud800.test/v1",
])
def test_endpoint_rejects_ambiguous_or_secret_bearing_urls_without_echoing_them(endpoint):
    with pytest.raises(GatewayError) as caught:
        normalize_endpoint(endpoint)
    assert caught.value.code == "invalid_endpoint"
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("protocol", ["", "unknown", None, [], {}])
def test_unknown_protocol_fails_before_url_processing(protocol):
    with pytest.raises(GatewayError) as caught:
        normalize_endpoint("https://api.example.test/v1", protocol)
    assert caught.value.code == "invalid_protocol"


def test_catalog_is_usable_without_importing_agent_or_consumer():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, "-c",
        "import sys; from model_gateway.catalog import provider_presets, normalize_endpoint; "
        "assert provider_presets(); assert normalize_endpoint('https://example.test/v1'); "
        "assert not any(n == 'agent' or n.startswith('agent.') or n == 'consumer' or "
        "n.startswith('consumer.') for n in sys.modules)"], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
