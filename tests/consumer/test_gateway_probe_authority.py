"""A revoked capability check cannot be replaced by a fresh text-only grant."""
import asyncio
from dataclasses import replace

import pytest

from agent.protocol import CoreError, ToolCall, canonical_json
from consumer.application import AgentApplication
from tests.consumer.test_application import configuration
from tests.gateway.test_transports import oai, sse, wire


async def test_cancelled_gateway_tool_probe_cannot_fall_back_and_activate(tmp_path):
    release = asyncio.Event()
    response = sse(oai({"tool_calls": [{"index": 0, "id": "probe", "type": "function",
        "function": {"name": "invoke", "arguments": canonical_json(ToolCall("consumer.probe", {}).as_dict())}}]},
        "tool_calls"), {"choices": [], "usage": {"prompt_tokens": 8, "completion_tokens": 42, "total_tokens": 50}}, b"[DONE]")
    async with wire([response], delay_headers=release) as (url, _, requests):
        endpoint = url + "/model-gateway/v1/chat/completions"
        app = AgentApplication(replace(configuration(tmp_path, origins=(url,)), gateway_endpoint=endpoint))
        await app.start()
        binding = asyncio.create_task(app.bind_gateway(endpoint, "stg_probe_authority", "conn/model", 32768, 1024, True))
        try:
            async with asyncio.timeout(5):
                while not requests:
                    await asyncio.sleep(.01)
            work = next(row for row in app.journal.list("work", app.scope)
                if row.value["kind"] == "provider_probe")
            app.core.scheduler.cancel(app.scope, work.id, reason="operator_revoked")
            release.set()
            with pytest.raises(CoreError) as caught:
                await asyncio.wait_for(binding, 5)
            assert caught.value.code == "provider_unavailable"
            assert len(requests) == 1
            providers = app.providers.inspect()
            assert len(providers) == 1 and providers[0]["tools"]
            assert not providers[0]["ready"] and not providers[0]["active"]
            assert providers[0]["probe"]["error"]["code"] in {"cancelled", "stale_authority", "authority_revoked"}
        finally:
            release.set()
            if not binding.done(): binding.cancel()
            await asyncio.gather(binding, return_exceptions=True)
            await app.close()
