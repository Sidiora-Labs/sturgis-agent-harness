"""Model activation preserves categorized failures without upstream secrets."""
import pytest

from agent.protocol import CoreError
from consumer.providers import probe_failure
from tests.consumer.test_api import PASSWORD, TOKEN, agent
from tests.consumer_integration.test_http import http_installation
from tests.gateway.test_transports import wire


@pytest.mark.parametrize("details, expected", [
    ({"provider_code": "provider_protocol"}, "stream the gateway could not read"),
    ({"provider_code": "authentication_error"}, "rejected the API key"),
    ({"provider_code": "insufficient_quota"}, "quota or credits"),
    ({"status": 401}, "rejected the API key"),
    ({"status": 402}, "quota or credits"),
    ({"status": 429}, "rate or quota limit"),
    ({"provider_code": "private-provider-secret", "status": "private-provider-secret"}, "model check failed"),
    ({"provider_code": ["private-provider-secret"], "status": True}, "model check failed"),
])
def test_probe_failure_uses_only_recognized_categories(details, expected):
    result = probe_failure(CoreError("provider_http", "private-provider-secret", **details))
    assert expected in result["message"]
    assert result["code"] == "provider_http"
    assert "private-provider-secret" not in str(result)


@pytest.mark.parametrize("upstream_status, expected", [
    (401, "rejected the API key"),
    (404, "could not find this model"),
    (429, "rate or quota limit"),
])
async def test_use_model_explains_upstream_failure_through_actual_gateway(tmp_path, upstream_status, expected):
    def upstream(request, count):
        if request["method"] == "GET":
            return {"data": [{"id": "check-model", "context_length": 8192,
                "top_provider": {"max_completion_tokens": 1024}, "supported_parameters": []}]}
        return upstream_status, {"error": {"message": "private-provider-secret"}}

    async with wire(upstream) as (url, _, _):
        async with http_installation(tmp_path, url) as (client, _):
            session = await client.post("/api/v1/bootstrap", json={
                "email": "owner@example.com", "password": PASSWORD, "token": TOKEN})
            client.headers["Authorization"] = "Bearer " + session.json()["data"]["token"]
            connection = await client.post("/api/v1/gateway/connections", json={
                "preset_id": "custom-openai", "name": "Model check", "endpoint": url + "/v1",
                "api_key": "test-credential", "models": ["check-model"]})
            assert connection.status_code == 200
            owner = await agent(client)
            selected = await client.post(f"/api/v1/agents/{owner['id']}/gateway-model", json={
                "model": connection.json()["data"]["id"] + "/check-model"})
            assert selected.status_code == 503
            error = selected.json()["error"]
            assert error["code"] == "provider_unavailable"
            assert expected in error["message"]
            assert "private-provider-secret" not in selected.text
