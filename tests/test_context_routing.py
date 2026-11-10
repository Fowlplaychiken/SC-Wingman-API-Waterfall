import asyncio
import json

import httpx
import pytest
from provider_fallback_skill.routing import ProviderWaterfall, RouteSpec, RoutingFailure
from provider_fallback_skill.server import create_app


def test_smaller_fallback_is_checked_before_sending_without_discarding_context():
    async def run():
        calls = []

        def handler(request):
            calls.append(request.url.host)
            return httpx.Response(402, json={"error": {"code": "insufficient_quota"}})

        router = ProviderWaterfall(
            (
                RouteSpec("primary", "https://primary.invalid/v1/chat/completions", "real-primary"),
                RouteSpec(
                    "local",
                    "http://127.0.0.1:9000/v1/chat/completions",
                    "real-local",
                    final_local=True,
                    context_window=400,
                    max_output_tokens=64,
                    tool_reserve_tokens=100,
                ),
            ),
            None,
            token_counter=lambda _: 240,
            client_factory=lambda **kw: httpx.AsyncClient(
                transport=httpx.MockTransport(handler), **kw
            ),
        )
        payload = {"messages": [{"role": "user", "content": "retain this"}]}
        with pytest.raises(RoutingFailure) as exc:
            await router.open(payload)
        assert exc.value.code == "context_budget_exceeded"
        assert calls == ["primary.invalid"]
        assert payload["messages"][0]["content"] == "retain this"
        assert router.status()["last_budget"]["route_id"] == "local"
        await router.aclose()

    asyncio.run(run())


def test_configured_real_model_is_advertised_and_tool_turn_uses_reserved_headroom():
    async def run():
        sent = []

        def handler(request):
            sent.append(json.loads(request.content))
            return httpx.Response(
                200,
                stream=httpx.ByteStream(b'{"choices": []}'),
                headers={"content-type": "application/json"},
            )

        route = RouteSpec(
            "primary",
            "https://primary.invalid/v1/chat/completions",
            "real-model",
            context_window=400,
            max_output_tokens=64,
            tool_reserve_tokens=100,
        )
        router = ProviderWaterfall(
            (route,),
            None,
            token_counter=lambda _: 240,
            client_factory=lambda **kw: httpx.AsyncClient(
                transport=httpx.MockTransport(handler), **kw
            ),
        )
        app = create_app(router, "test-bearer")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://local",
            headers={"authorization": "Bearer test-bearer"},
        ) as client:
            models = (await client.get("/v1/models")).json()["data"]
            assert {m["id"] for m in models} == {"waterfall", "real-model"}
            result = await client.post(
                "/v1/chat/completions", json={"messages": [{"role": "user", "content": "private"}]}
            )
            assert result.status_code == 400
            assert "private" not in result.text and sent == []
            result = await client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "tool", "tool_call_id": "c1", "content": "result"}]},
            )
            assert result.status_code == 200
            assert sent[0]["max_tokens"] == 64
            assert sent[0]["model"] == "real-model"
            assert router.status()["last_budget"]["tool_continuation"] is True
        await router.aclose()

    asyncio.run(run())


def test_hosted_support_keeps_its_native_limits():
    with pytest.raises(ValueError, match="Wingman manages"):
        RouteSpec(
            "hosted",
            "https://api.wingman-ai.com/api/v1/support/completions",
            "hosted",
            wire_protocol="wingman_support",
            context_window=100000,
        )
