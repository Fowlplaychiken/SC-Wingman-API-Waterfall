import asyncio
import json

import httpx
import pytest
from provider_fallback_skill.routing import (
    ProviderWaterfall,
    RouteSpec,
    RoutingFailure,
)
from provider_fallback_skill.server import create_app


def native_response():
    return {
        "id": "msg",
        "type": "message",
        "model": "claude-test",
        "stop_reason": "tool_use",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "signature"},
            {"type": "tool_use", "id": "call-1", "name": "lookup", "input": {"x": 1}},
        ],
        "usage": {"input_tokens": 4, "output_tokens": 3},
    }


def make_router(handler, routes, assistance=None):
    async def secret(_):
        return "test-key"

    return ProviderWaterfall(
        routes,
        secret,
        assistance=assistance,
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )


def test_native_messages_tool_roundtrip_over_http():
    async def run():
        observed = []

        def handler(request):
            observed.append((dict(request.headers), json.loads(request.content)))
            return httpx.Response(200, json=native_response())

        route = RouteSpec(
            "native",
            "https://api.anthropic.com/v1/messages",
            "claude-test",
            "secret",
            wire_protocol="anthropic_messages",
            request_overrides={
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "low"},
            },
        )
        router = make_router(handler, (route,))
        app = create_app(router, "bridge")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost:4141",
            headers={"authorization": "Bearer bridge"},
        ) as client:
            p = {"messages": [{"role": "user", "content": "lookup"}]}
            first = await client.post("/v1/chat/completions", json=p)
            assert first.status_code == 200
            message = first.json()["choices"][0]["message"]
            p["messages"].extend(
                [message, {"role": "tool", "tool_call_id": "call-1", "content": "result"}]
            )
            second = await client.post("/v1/chat/completions", json=p)
            assert second.status_code == 200
        assert observed[0][0]["x-api-key"] == "test-key"
        assert observed[0][0]["anthropic-version"] == "2023-06-01"
        assert observed[0][1]["thinking"]["display"] == "omitted"
        assert observed[1][1]["messages"][1]["content"] == native_response()["content"]
        assert observed[1][1]["messages"][2]["content"][0]["tool_use_id"] == "call-1"
        await router.aclose()

    asyncio.run(run())


def test_native_credit_fallback_strips_private_state_and_header_without_key():
    async def run():
        seen = []

        def handler(request):
            seen.append(request)
            if request.url.host == "api.anthropic.com":
                return httpx.Response(402, json={"error": {"type": "credit_exhausted"}})
            return httpx.Response(200, json={"choices": []})

        routes = (
            RouteSpec(
                "native",
                "https://api.anthropic.com/v1/messages",
                "m",
                wire_protocol="anthropic_messages",
            ),
            RouteSpec("local", "http://localhost:9000/v1/chat/completions", "q"),
        )
        router = make_router(handler, routes)
        payload = {
            "messages": [
                {
                    "role": "assistant",
                    "content": "hello",
                    "wingman_native_state": {
                        "version": 1,
                        "model": "m",
                        "blocks": [{"type": "text", "text": "hello"}],
                    },
                }
            ]
        }
        upstream = await router.open(payload)
        assert upstream.route_id == "local"
        assert seen[0].headers["anthropic-version"] == "2023-06-01"
        assert "x-api-key" not in seen[0].headers
        assert "wingman_native_state" not in json.loads(seen[1].content)["messages"][0]
        await upstream.aclose()
        await router.aclose()

    asyncio.run(run())


def test_close_during_assistance_prevents_dispatch():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        class Assist:
            async def assist(self, payload, confidence):
                entered.set()
                await release.wait()
                return payload, {"status": "unselected"}

            def reset(self):
                pass

        router = make_router(
            lambda request: calls.append(request),
            (RouteSpec("r", "https://example.com/v1/chat/completions", "m", jev_assist=True),),
            Assist(),
        )
        pending = asyncio.create_task(router.open({"messages": []}))
        await entered.wait()
        await router.aclose()
        release.set()
        with pytest.raises(RoutingFailure, match="bridge_closed"):
            await pending
        assert not calls

    asyncio.run(run())


def test_native_fallback_rebuilds_visible_history_for_a_different_model():
    async def run():
        seen = []

        def handler(request):
            body = json.loads(request.content)
            seen.append(body)
            if len(seen) == 2:
                return httpx.Response(402, json={"error": {"type": "credit_exhausted"}})
            return httpx.Response(200, json=native_response())

        routes = (
            RouteSpec(
                "first",
                "https://first.example/v1/messages",
                "m",
                wire_protocol="anthropic_messages",
            ),
            RouteSpec(
                "second",
                "https://second.example/v1/messages",
                "other",
                wire_protocol="anthropic_messages",
            ),
        )
        router = make_router(handler, routes)
        payload = {"messages": [{"role": "user", "content": "lookup"}]}
        first = await router.open(payload)
        message = json.loads(await first.response.aread())["choices"][0]["message"]
        await first.aclose()
        payload["messages"].extend(
            [message, {"role": "tool", "tool_call_id": "call-1", "content": "result"}]
        )
        second = await router.open(payload)
        assert second.route_id == "second"
        assert seen[1]["messages"][1]["content"] == native_response()["content"]
        assert seen[2]["messages"][1]["content"] == [native_response()["content"][1]]
        assert "signature" not in json.dumps(seen[2])
        assert "wingman_native_state" in payload["messages"][1]  # Original history is untouched.
        await second.aclose()
        await router.aclose()

    asyncio.run(run())
