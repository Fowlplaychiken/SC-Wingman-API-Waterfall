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


def response(status=200, body=None):
    raw = json.dumps(body if body is not None else {"choices": []}).encode()
    return httpx.Response(
        status, stream=httpx.ByteStream(raw), headers={"content-type": "application/json"}
    )


async def secret(_name):
    return "fixture-only-key"


def factory(handler):
    return lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)


def routes():
    return (
        RouteSpec("claude", "https://claude.invalid/v1/chat/completions", "h", "claude-key"),
        RouteSpec("gpt", "https://gpt.invalid/v1/chat/completions", "g", "gpt-key"),
        RouteSpec("local", "http://192.168.1.2:8000/v1/chat/completions", "q", final_local=True),
    )


def test_anthropic_credit_400_is_scoped_sticky_and_overload_is_temporary():
    async def run():
        actual = (
            RouteSpec("anthropic", "https://api.anthropic.com/v1/chat/completions", "h", "k"),
            RouteSpec("local", "http://127.0.0.1:9000/v1/chat/completions", "q"),
        )
        requests = []
        mode = "credit"

        def handler(request):
            requests.append(request.url.host)
            if request.url.host != "api.anthropic.com":
                return response()
            if mode == "credit":
                return response(
                    400,
                    {
                        "error": {
                            "type": "invalid_request_error",
                            "message": (
                                "Your credit balance is too low to access the Anthropic API."
                                " Please add credit."
                            ),
                        }
                    },
                )
            if mode == "overload":
                return response(529, {"error": {"type": "overloaded_error"}})
            return response(
                400, {"error": {"type": "invalid_request_error", "message": "Bad model"}}
            )

        router = ProviderWaterfall(actual, secret, client_factory=factory(handler))
        for _ in range(2):
            upstream = await router.open({"messages": []})
            assert upstream.route_id == "local"
            await upstream.aclose()
        assert requests == ["api.anthropic.com", "127.0.0.1", "127.0.0.1"]
        assert router.status()["routes"][0]["credit_exhausted"] is True
        router.reset()
        mode = "other"
        with pytest.raises(RoutingFailure, match="upstream_request_rejected"):
            await router.open({"messages": []})
        assert not router.status()["routes"][0]["credit_exhausted"]
        mode = "overload"
        upstream = await router.open({"messages": []})
        await upstream.aclose()
        state = router.status()["routes"][0]
        assert state["reason"] == "temporary_unavailable" and not state["credit_exhausted"]
        await router.aclose()

        other = (RouteSpec("other", "https://other.invalid/v1/chat/completions", "h"),)
        router = ProviderWaterfall(
            other,
            secret,
            client_factory=factory(
                lambda _: response(
                    400,
                    {
                        "error": {
                            "type": "invalid_request_error",
                            "message": ("Your credit balance is too low to access the Anthropic "
                                        "API."),
                        }
                    },
                )
            ),
        )
        with pytest.raises(RoutingFailure, match="upstream_request_rejected"):
            await router.open({"messages": []})
        assert not router.status()["routes"][0]["credit_exhausted"]
        await router.aclose()

    asyncio.run(run())


def test_credit_skip_is_sticky_and_reset_reenables_without_losing_payload():
    async def run():
        requests = []

        def handler(request):
            requests.append(request)
            return (
                response(402, {"error": {"code": "insufficient_quota"}})
                if request.url.host == "claude.invalid"
                else response()
            )

        router = ProviderWaterfall(routes(), secret, client_factory=factory(handler))
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}
                    ],
                }
            ],
            "tools": [{"type": "function", "function": {"name": "lookup"}}],
            "stream": False,
        }
        for _ in range(2):
            upstream = await router.open(payload)
            assert upstream.route_id == "gpt"
            sent = json.loads(requests[-1].content)
            assert sent["messages"] == payload["messages"] and sent["tools"] == payload["tools"]
            assert sent["model"] == "g"
            await upstream.aclose()
        assert [r.url.host for r in requests] == ["claude.invalid", "gpt.invalid", "gpt.invalid"]
        assert router.status()["routes"][0]["credit_exhausted"] is True
        assert "fixture-only-key" not in json.dumps(router.status())
        router.reset("claude")
        upstream = await router.open(payload)
        await upstream.aclose()
        assert [r.url.host for r in requests][-2:] == ["claude.invalid", "gpt.invalid"]
        await router.aclose()
        assert not router._active

    asyncio.run(run())


def test_rate_limit_falls_through_to_local_but_policy_refusal_does_not():
    async def run():
        calls = []

        def handler(request):
            calls.append(request.url.host)
            return response(429) if request.url.scheme == "https" else response()

        router = ProviderWaterfall(routes(), secret, client_factory=factory(handler))
        upstream = await router.open({"messages": []})
        assert upstream.route_id == "local" and calls == [
            "claude.invalid",
            "gpt.invalid",
            "192.168.1.2",
        ]
        assert not any(row["credit_exhausted"] for row in router.status()["routes"])
        await upstream.aclose()
        await router.aclose()
        calls.clear()

        def denied(request):
            calls.append(request.url.host)
            return response(403, {"error": {"code": "content_filter"}})

        router = ProviderWaterfall(routes(), secret, client_factory=factory(denied))
        with pytest.raises(RoutingFailure, match="provider_refused"):
            await router.open({"messages": []})
        assert calls == ["claude.invalid"]
        await router.aclose()

    asyncio.run(run())


def test_ambiguous_timeout_is_not_replayed_and_clients_close():
    async def run():
        clients = []
        calls = []

        def handler(request):
            calls.append(request.url.host)
            raise httpx.ReadTimeout("fixture", request=request)

        def make(**kwargs):
            client = factory(handler)(**kwargs)
            clients.append(client)
            return client

        router = ProviderWaterfall(routes(), secret, client_factory=make)
        with pytest.raises(RoutingFailure, match="timeout_unknown"):
            await router.open({"messages": []})
        assert len(calls) == 1 and all(c.is_closed for c in clients)

    asyncio.run(run())


def test_any_number_of_routes_and_copyable_discovery():
    async def run():
        selected = tuple(
            RouteSpec(f"r{i}", f"https://r{i}.invalid/v1/chat/completions", f"m{i}")
            for i in range(40)
        )

        def handler(request):
            return response(body={"data": [{"id": "new-model"}, {"id": "old-model"}]})

        router = ProviderWaterfall(selected, secret, client_factory=factory(handler))
        assert len(router.status()["routes"]) == 40
        assert await router.discover_models("r39") == ["new-model", "old-model"]
        await router.aclose()

    asyncio.run(run())


def test_proxy_auth_and_nonstream_and_sse_preserve_messages_and_status():
    async def run():
        sent = []

        def handler(request):
            sent.append(json.loads(request.content))
            return (
                response(402)
                if request.url.host == "claude.invalid"
                else httpx.Response(
                    200,
                    stream=httpx.ByteStream(b'data: {"choices":[]}\n\ndata: [DONE]\n\n'),
                    headers={"content-type": "text/event-stream"},
                )
            )

        router = ProviderWaterfall(routes(), secret, client_factory=factory(handler))
        app = create_app(router, "local-test-bearer")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:49000"
        ) as client:
            assert (await client.get("/status")).status_code == 401
            headers = {"Authorization": "Bearer local-test-bearer"}
            result = await client.post(
                "/v1/chat/completions", json={"messages": [], "stream": True}, headers=headers
            )
            assert result.status_code == 200 and "[DONE]" in result.text
            assert result.headers["x-wingman-waterfall-route"] == "gpt"
            assert not router._active
            malformed = await client.post(
                "/v1/chat/completions", json={"messages": [], "stream": "yes"}, headers=headers
            )
            assert malformed.status_code == 400
        await router.aclose()

    asyncio.run(run())
