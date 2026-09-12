"""Independent provider presets and unambiguous inference base URL normalization.

Presets describe connection defaults, never a promise of model availability.
Discovery and the selected model determine capabilities; the conservative limits
are used only when the upstream does not publish metadata. See the pinned source
and pricing audit in ``.research/9router-gateway.md``.
"""
from __future__ import annotations

from copy import deepcopy
import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit


PROTOCOLS = ("openai", "anthropic", "gemini", "openai-responses")
CATALOG_REVIEWED_AT = "2026-09-12"


def _preset(provider_id, name, endpoint, category, description, key_url="", *,
            protocol="openai", auth="api_key", models_endpoint=None,
            integration_status="native", **metadata):
    """Create one descriptor; mutable nested fields are private to the registry."""
    row = {
        "id": provider_id, "name": name, "endpoint": endpoint,
        "protocol": protocol, "auth": auth, "category": category,
        "description": description, "key_url": key_url,
        "auth_modes": ["none", "api_key"] if auth == "none" else ["api_key"],
        "integration_status": integration_status,
        "defaults": {"context_limit": 8192, "output_limit": 1024, "tools": False},
        **metadata,
    }
    if models_endpoint is not None:
        row["models_endpoint"] = models_endpoint
    return row


_PRESETS = (
    _preset("opencode", "OpenCode Free", "https://opencode.ai/zen/v1", "free_tier",
            "Try currently available free models without an API key. Availability and promotional models change.",
            "https://opencode.ai/docs/zen", auth="none", model_filter="opencode_free",
            models_endpoint="https://opencode.ai/zen/v1/models"),
    _preset("groq", "Groq", "https://api.groq.com/openai/v1", "free_tier",
            "Free plan with model and account quotas. Connect your Groq API key.",
            "https://console.groq.com/keys"),
    _preset("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta", "free_tier",
            "Eligible models have a free tier with quotas. Other models and higher usage may require billing.",
            "https://aistudio.google.com/apikey", protocol="gemini"),
    _preset("cerebras", "Cerebras", "https://api.cerebras.ai/v1", "free_tier",
            "Start with a free API key; model availability and usage limits depend on your account.",
            "https://cloud.cerebras.ai/platform/api-keys"),
    _preset("mistral", "Mistral AI", "https://api.mistral.ai/v1", "free_tier",
            "Free-mode access is plan and model dependent. Connect your Mistral API key to check availability.",
            "https://console.mistral.ai/api-keys"),
    _preset("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "free_tier",
            "Free and paid models in one catalog. Free models have quotas; check the imported model's pricing.",
            "https://openrouter.ai/settings/keys"),
    _preset("openai", "OpenAI", "https://api.openai.com/v1", "paid",
            "Connect an OpenAI API account. API billing is separate from a ChatGPT subscription.",
            "https://platform.openai.com/api-keys"),
    _preset("anthropic", "Anthropic", "https://api.anthropic.com/v1", "paid",
            "Connect a Claude API key from the Anthropic Console.",
            "https://console.anthropic.com/settings/keys", protocol="anthropic"),
    _preset("deepseek", "DeepSeek", "https://api.deepseek.com", "paid",
            "Connect your DeepSeek API account and import its current models.",
            "https://platform.deepseek.com/api_keys"),
    _preset("xai", "xAI", "https://api.x.ai/v1", "paid",
            "Connect an xAI API key for Grok models.", "https://console.x.ai"),
    _preset("together", "Together AI", "https://api.together.xyz/v1", "paid",
            "Hosted open models using your Together API account.", "https://api.together.ai/settings/api-keys"),
    _preset("fireworks", "Fireworks AI", "https://api.fireworks.ai/inference/v1", "paid",
            "Serverless models using your Fireworks account and model access.",
            "https://fireworks.ai/account/api-keys"),
    _preset("nvidia", "NVIDIA", "https://integrate.api.nvidia.com/v1", "paid",
            "NVIDIA API Catalog access. Evaluation access and production terms depend on your account.",
            "https://build.nvidia.com"),
    _preset("sambanova", "SambaNova", "https://api.sambanova.ai/v1", "paid",
            "Connect your SambaNova API account. Access and credits depend on your plan.",
            "https://cloud.sambanova.ai"),
    _preset("huggingface", "Hugging Face", "https://router.huggingface.co/v1", "gateway",
            "Inference Providers through your Hugging Face token. Usage credits and model pricing vary.",
            "https://huggingface.co/settings/tokens"),
    _preset("moonshot", "Moonshot AI", "https://api.moonshot.ai/v1", "paid",
            "Connect the Moonshot API for Kimi models using an API key.",
            "https://platform.moonshot.ai/console/api-keys"),
    _preset("minimax", "MiniMax", "https://api.minimax.io/anthropic/v1", "paid",
            "MiniMax's Anthropic-compatible model API using your account key.",
            "https://platform.minimax.io", protocol="anthropic"),
    _preset("zai", "Z.AI", "https://api.z.ai/api/paas/v4", "paid",
            "The standard Z.AI API for GLM models. Use a custom endpoint for a separate coding plan.",
            "https://z.ai/manage-apikey/apikey-list"),
    _preset("siliconflow", "SiliconFlow", "https://api.siliconflow.com/v1", "paid",
            "Connect the international SiliconFlow API. Model pricing depends on your account.",
            "https://cloud.siliconflow.com/account/ak"),
    _preset("nebius", "Nebius", "https://api.studio.nebius.ai/v1", "paid",
            "Connect your Nebius model API account.", "https://studio.nebius.com"),
    _preset("perplexity", "Perplexity", "https://api.perplexity.ai", "paid",
            "Connect the Perplexity Sonar API. Some APIs require manually entering the documented model ID.",
            "https://www.perplexity.ai/settings/api"),
    _preset("cohere", "Cohere", "https://api.cohere.ai/compatibility/v1", "paid",
            "Cohere's OpenAI-compatible Chat API. Trial access and model limits depend on your account.",
            "https://dashboard.cohere.com/api-keys"),
    _preset("chutes", "Chutes", "https://llm.chutes.ai/v1", "paid",
            "Connect Chutes model inference with your account API key.", "https://chutes.ai"),
    _preset("hyperbolic", "Hyperbolic", "https://api.hyperbolic.xyz/v1", "paid",
            "Connect Hyperbolic's hosted model API.", "https://app.hyperbolic.xyz"),
    _preset("vercel", "Vercel AI Gateway", "https://ai-gateway.vercel.sh/v1", "gateway",
            "Import models from your Vercel AI Gateway account. Credit and billing rules apply.",
            "https://vercel.com/ai-gateway"),
    _preset("ollama", "Ollama", "http://127.0.0.1:11434/v1", "local",
            "Use models already downloaded to your Ollama server. No key is needed for its default local setup.",
            "https://ollama.com/download", auth="none"),
    _preset("lmstudio", "LM Studio", "http://127.0.0.1:1234/v1", "local",
            "Start LM Studio's local server, then import the models it exposes.",
            "https://lmstudio.ai/docs/developer", auth="none"),
    _preset("vllm", "vLLM", "http://127.0.0.1:8000/v1", "local",
            "Connect a running vLLM server. Tool support depends on the model and server configuration.",
            "https://docs.vllm.ai", auth="none"),
    _preset("llama-cpp", "llama.cpp", "http://127.0.0.1:8080/v1", "local",
            "Connect a running llama-server instance and import its loaded model.",
            "https://github.com/ggml-org/llama.cpp/tree/master/tools/server", auth="none"),
    _preset("9router", "9router", "http://127.0.0.1:20128/v1", "gateway",
            "Import models and routing groups from your existing 9router gateway, including accounts connected there.",
            "https://github.com/decolua/9router", auth="none", integration_status="external_gateway",
            preserves_model_namespace=True),
    _preset("custom-openai", "Custom OpenAI-compatible", "", "custom",
            "Connect any compatible Chat Completions server with its base endpoint and optional API key.",
            auth="none"),
    _preset("custom-anthropic", "Custom Anthropic-compatible", "", "custom",
            "Connect a compatible Messages server with its base endpoint and optional API key.",
            protocol="anthropic", auth="none"),
    _preset("custom-gemini", "Custom Gemini-compatible", "", "custom",
            "Connect a Gemini-compatible server with its base endpoint and optional API key.",
            protocol="gemini", auth="none"),
    _preset("custom-responses", "Custom Responses-compatible", "", "custom",
            "Connect an OpenAI Responses-compatible server with its base endpoint and optional API key.",
            protocol="openai-responses", auth="none"),
)


def provider_presets() -> list[dict]:
    """Return independent public descriptors without touching storage or a network."""
    return deepcopy(list(_PRESETS))


def provider_preset(provider_id: str) -> dict | None:
    """Find a preset by its stable ID; unknown IDs never select a fallback provider."""
    return next((deepcopy(row) for row in _PRESETS if row["id"] == provider_id), None)


def normalize_endpoint(endpoint: str, protocol: str = "openai") -> str:
    """Validate a credential-free HTTP base and remove an exact operation suffix.

    Prefix paths and root-level APIs are preserved: blindly appending ``/v1``
    would break providers such as Perplexity and user-defined reverse proxies.
    This performs syntax validation only; the gateway network policy authorizes
    the resolved destination before every request.
    """
    from model_gateway.errors import GatewayError

    if protocol not in PROTOCOLS:
        raise GatewayError("invalid_protocol", "Choose a supported provider protocol.")
    if not isinstance(endpoint, str) or not endpoint.strip() or len(endpoint) > 2048:
        raise GatewayError("invalid_endpoint", "Enter a valid provider endpoint URL.")
    endpoint = endpoint.strip()
    if any(ord(char) < 33 or ord(char) == 127 for char in endpoint) or any(char in endpoint for char in "\\?#"):
        raise GatewayError("invalid_endpoint", "Use an endpoint without spaces, query parameters or a fragment.")
    try:
        parts = urlsplit(endpoint)
        host, port = parts.hostname, parts.port
        if (parts.scheme not in {"http", "https"} or not host or parts.username is not None
                or parts.password is not None or "%" in host or not host.strip(".")
                or port is not None and not 0 < port < 65536):
            raise ValueError("Invalid endpoint")
        if ":" in host:
            host = f"[{ipaddress.IPv6Address(host).compressed}]"
        else:
            host = host.encode("idna").decode("ascii").lower()
        authority = host + (f":{port}" if port is not None else "")
    except (ValueError, UnicodeError):
        raise GatewayError("invalid_endpoint", "Use an HTTP or HTTPS endpoint without embedded credentials.") from None
    path = parts.path.rstrip("/")
    suffixes = {
        "openai": ("/chat/completions", "/models"),
        "openai-responses": ("/responses", "/models"),
        "anthropic": ("/messages", "/models"),
        "gemini": ("/models",),
    }
    if protocol == "gemini":
        path = re.sub(r"/models/[^/]+:(?:streamGenerateContent|generateContent)$", "", path)
    for suffix in suffixes[protocol]:
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    return urlunsplit((parts.scheme, authority, path, "", ""))
