"""Real gateway discovery, routing, model import, keys and consumption."""
import asyncio

import pytest

from model_gateway.errors import GatewayError
from model_gateway.service import ModelGateway
from tests.gateway.test_transports import wire, sse, oai, payload


CATALOG = {"data": [{"id": "model-a", "name": "Model A", "context_length": 32768, "max_output_tokens": 1024,
                     "supported_parameters": ["tools"]}, {"id": "model-b"}]}
ANSWER = sse(oai({"content": "Gateway response"}, "stop"), {"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}}, b"[DONE]")


def server(request, count):
    return CATALOG if request["method"] == "GET" else ANSWER


async def connect(service, tenant, endpoint, name="Local"):
    return await service.connect(tenant, preset_id="custom-openai", name=name, endpoint=endpoint + "/v1", api_key="provider-secret", models=["model-a", "model-b"])


async def test_real_discovery_connect_inference_refresh_and_persistence(tmp_path):
    async with wire(server) as (url, policy, requests):
        gateway = ModelGateway(tmp_path, network_policy=policy)
        discovered = await gateway.discover("alice", preset_id="custom-openai", endpoint=url+"/v1", api_key="provider-secret")
        assert discovered["source"] == "live" and len(discovered["models"]) == 2
        connection = await connect(gateway, "alice", url)
        assert "secret" not in str(connection) and connection["credential_configured"]
        model = gateway.models("alice")[0]
        assert model["tools"] and model["context_limit"] == 32768
        key = gateway.create_key("alice", name="Client", models=[model["id"]])
        answer = await gateway.complete(key["key"], payload(model=model["id"]))
        assert answer["choices"][0]["message"]["content"] == "Gateway response"
        assert answer["usage"]["total_tokens"] == 6
        assert requests[-1]["body"]["model"] == "model-a"
        assert requests[-1]["headers"]["authorization"] == "bearer provider-secret"
        assert gateway.store.usage("alice")["total_tokens"] == 6
        assert not gateway.connections("bob")
        with pytest.raises(GatewayError): await gateway.complete(key["key"], payload(model=connection["id"]+"/model-b"))
        refreshed = await gateway.refresh("alice", connection["id"], 1)
        assert refreshed["revision"] == 2
        assert ModelGateway(tmp_path).connections("alice")[0]["models"] == refreshed["models"]


async def test_key_never_follows_endpoint_or_provider_change(tmp_path):
    async with wire(server) as (url, policy, requests):
        gateway = ModelGateway(tmp_path, network_policy=policy)
        row = await connect(gateway, "alice", url)
        for args in ({"preset_id": "groq", "endpoint": "https://api.groq.com/openai/v1"},
                     {"preset_id": "custom-openai", "endpoint": url+"/different"}):
            with pytest.raises(GatewayError, match="Enter the API key again"):
                await gateway.discover("alice", connection_id=row["id"], **args)
        assert len(requests) == 1
        with pytest.raises(GatewayError): await gateway.refresh("alice", row["id"], 0)
        with pytest.raises(GatewayError): await gateway.connect("alice", connection_id=row["id"], expected_revision=0,
            preset_id="custom-openai", endpoint=url+"/v1", name="Changed", models=["model-a"])


async def test_priority_failover_cooldown_and_round_robin_are_gateway_owned(tmp_path):
    def upstream(request, count):
        if request["method"] == "GET": return CATALOG
        if request["body"]["model"] == "model-a": return 429, {"secret": "must not leak"}
        return ANSWER
    async with wire(upstream) as (url, policy, requests):
        gateway = ModelGateway(tmp_path, network_policy=policy)
        row = await connect(gateway, "a", url)
        models = [m["id"] for m in gateway.models("a")]
        route = gateway.save_route("a", id="daily", name="Daily", models=models)
        key = gateway.create_key("a", name="Daily client", models=["daily"])
        assert (await gateway.complete(key["key"], payload(model="daily")))["model"] == "daily"
        assert [r["body"]["model"] for r in requests if r["method"] == "POST"] == ["model-a", "model-b"]
        assert gateway.store.usage("a")["failures"] == 1
        await gateway.complete(key["key"], payload(model="daily"))
        assert len(requests) == 4
        gateway.save_route("a", id="daily", name="Daily", models=models, strategy="round_robin", expected_revision=route["revision"])
        principal = gateway.store.authenticate(key["key"])
        assert gateway._targets(principal, "daily")[0][0] == models[0]
        assert gateway._targets(principal, "daily")[0][0] == models[1]
        gateway.store.delete_connection("a", row["id"], row["revision"])
        assert not gateway.models("a")


async def test_no_failover_after_visible_output_and_preserve_attempt_usage(tmp_path):
    def upstream(request, count):
        return CATALOG if request["method"] == "GET" else sse(oai({"content": "Visible"}))
    async with wire(upstream) as (url, policy, requests):
        gateway = ModelGateway(tmp_path, network_policy=policy)
        await connect(gateway, "a", url)
        gateway.save_route("a", id="daily", name="Daily", models=[m["id"] for m in gateway.models("a")])
        key = gateway.create_key("a", name="Client")
        stream = gateway.stream(key["key"], payload(model="daily"))
        assert (await anext(stream))["choices"][0]["delta"]["content"] == "Visible"
        with pytest.raises(GatewayError): await anext(stream)
        assert len(requests) == 2 and gateway.store.usage("a")["requests"] == 1


async def test_cancellation_releases_capacity(tmp_path):
    async with wire(server) as (url, policy, requests):
        gateway = ModelGateway(tmp_path, network_policy=policy, max_concurrency=1)
        await connect(gateway, "a", url)
        key = gateway.create_key("a", name="Client")
        event = asyncio.Event(); event.set()
        with pytest.raises(GatewayError, match="cancelled"):
            await anext(gateway.stream(key["key"], payload(model=gateway.models("a")[0]["id"]), cancel=event))
        assert gateway._slots._value == 1 and len(requests) == 1


@pytest.mark.parametrize("params", [{"model": ""}, {"messages": []}, {"messages": ["hi"]}, {"stream": "yes"}, {"max_tokens": -1},
                                   {"max_completion_tokens": True}, {"n": 2}, {"tools": "invoke"}])
async def test_invalid_inference_rejected_before_network(tmp_path, params):
    gateway = ModelGateway(tmp_path)
    key = gateway.store.create_key("a", "Client")
    with pytest.raises(GatewayError): await gateway.complete(key["key"], payload(**params))


@pytest.mark.parametrize("models", [[], ["bad"], ["model-a", "model-a"], "all"])
async def test_import_models_must_exist_and_be_bounded(tmp_path, models):
    async with wire(server) as (url, policy, requests):
        gateway = ModelGateway(tmp_path, network_policy=policy)
        with pytest.raises(GatewayError):
            await gateway.connect("a", preset_id="custom-openai", name="Local", endpoint=url+"/v1", api_key="key", models=models)
        assert gateway.connections("a") == []


@pytest.mark.parametrize("value", [None, -1, True, 129])
def test_service_concurrency_is_bounded(tmp_path, value):
    with pytest.raises(GatewayError): ModelGateway(tmp_path, max_concurrency=value)
