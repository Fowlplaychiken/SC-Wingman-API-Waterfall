import asyncio
import json

import httpx
import pytest
from provider_fallback_skill.routing import (
    ProviderWaterfall,
    RouteSpec,
    RoutingFailure,
)


def router_for(handler, *, policy=("credit_exhausted",), callback=None):
    async def secret(_):
        return "private-test-token"

    return ProviderWaterfall(
        (
            RouteSpec(
                "primary",
                "https://first.example/v1/chat/completions",
                "first",
                "key",
                fallback_on=policy,
            ),
            RouteSpec("backup", "https://second.example/v1/chat/completions", "second"),
        ),
        secret,
        client_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw),
        on_event=callback,
    )


def test_credits_only_preserves_service_error_then_primary_can_recover_without_reset():
    async def run():
        calls, events = [], []

        def handler(request):
            calls.append(request.url.host)
            if len(calls) == 1:
                return httpx.Response(
                    503,
                    json={"error": {"message": "private-test-token private prompt"}},
                    headers={"request-id": "req_123"},
                )
            return httpx.Response(200, json={"choices": []})

        router = router_for(handler, callback=events.append)
        with pytest.raises(RoutingFailure) as caught:
            await router.open({"messages": []})
        assert caught.value.status == 503 and caught.value.request_id
        upstream = await router.open({"messages": []})
        assert calls == ["first.example", "first.example"]
        assert router.status()["last_selected_route"] == "primary"
        assert router.status()["last_failure"]["provider_request_id"] == "req_123"
        assert "private-test-token" not in json.dumps([router.status(), events])
        assert "private prompt" not in json.dumps([router.status(), events])
        await upstream.aclose()
        await router.aclose()

    asyncio.run(run())


def test_fallback_is_visible_and_credit_failure_remains_in_status():
    async def run():
        calls, events = [], []

        def handler(request):
            calls.append(request.url.host)
            return httpx.Response(
                402 if request.url.host == "first.example" else 200,
                json={"error": {"code": "insufficient_credits"}},
            )

        router = router_for(handler, callback=events.append)
        for _ in range(2):
            upstream = await router.open({"messages": []})
            await upstream.aclose()
        assert calls == ["first.example", "second.example", "second.example"]
        assert [x["kind"] for x in events] == ["failure", "selection"]
        assert events[-1]["fallback"] and events[-1]["model"] == "second"
        assert router.status()["last_failure"]["reason"] == "credit_exhausted"
        assert router.status()["last_selected_route"] == "backup"
        await router.aclose()

    asyncio.run(run())


def test_manual_mode_does_not_silently_fall_through_on_later_requests():
    async def run():
        calls = []

        def handler(request):
            calls.append(request.url.host)
            return httpx.Response(402, json={"error": {"code": "insufficient_credits"}})

        router = router_for(handler, policy=())
        for _ in range(2):
            with pytest.raises(RoutingFailure) as caught:
                await router.open({"messages": []})
            assert caught.value.status == 402
        assert calls == ["first.example"]
        await router.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["http_504", "client_timeout"])
def test_ambiguous_timeouts_are_correlated_and_never_fall_back(failure):
    async def run():
        calls, events = [], []

        def handler(request):
            calls.append(request.url.host)
            if failure == "client_timeout":
                raise httpx.ReadTimeout("private-test-token private prompt", request=request)
            return httpx.Response(504, json={"error": {"message": "private prompt"}})

        router = router_for(handler, policy=("temporary_unavailable",), callback=events.append)
        with pytest.raises(RoutingFailure) as caught:
            await router.open({"messages": []})
        assert caught.value.status == 504 and caught.value.code == "upstream_timeout_unknown"
        assert calls == ["first.example"]
        assert len(events) == 1 and events[0]["request_id"] == caught.value.request_id
        assert events[0]["reason"] == "upstream_timeout_unknown"
        assert "private" not in json.dumps([events, router.status()])
        await router.aclose()

    asyncio.run(run())


def test_closed_router_has_unique_correlated_failure():
    async def run():
        events = []
        router = router_for(lambda request: httpx.Response(200), callback=events.append)
        await router.aclose()
        with pytest.raises(RoutingFailure) as caught:
            await router.open({"messages": []})
        assert caught.value.code == "bridge_closed" and caught.value.request_id
        assert len(events) == 1 and events[0]["request_id"] == caught.value.request_id

    asyncio.run(run())
