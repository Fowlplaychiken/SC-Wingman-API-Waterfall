import asyncio
import json

import httpx
import pytest
from provider_fallback_skill.routing import (
    AccountHealth,
    ProviderWaterfall,
    RouteSpec,
    RoutingFailure,
)
from provider_fallback_skill.server import create_support_app

PAYLOAD = {
    "messages": [
        {"role": "system", "content": "Summarize"},
        {"role": "user", "content": "A private conversation"},
    ],
    "temperature": 0.3,
    "chat_template_kwargs": {"enable_thinking": False},
    "max_tokens": 80,
}
REPLY = {
    "choices": [{"message": {"role": "assistant", "content": "Summary"}, "finish_reason": "stop"}]
}


def make_router(handler, **kwargs):
    async def secret(_):
        return "private-key"

    routes = (
        RouteSpec(
            "hosted",
            "https://wingman.example/api/v1/support/completions",
            "a",
            "hosted-key",
            wire_protocol="wingman_support",
            fallback_on=("credit_exhausted",),
        ),
        RouteSpec(
            "own",
            "https://own.example/v1/chat/completions",
            "b",
            "own-key",
            omit_request_fields=("temperature", "chat_template_kwargs"),
        ),
    )
    return ProviderWaterfall(
        routes,
        secret,
        role="support",
        client_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw),
        **kwargs,
    )


def test_hosted_quota_fallback_shape_notices_and_shared_account_exhaustion():
    async def run():
        calls, events = [], []

        def handler(request):
            calls.append((request.url.host, json.loads(request.content)))
            if request.url.host == "wingman.example":
                return httpx.Response(
                    429,
                    json={"code": "support_allowance_exhausted"},
                    headers={"request-id": "hosted_123"},
                )
            return httpx.Response(200, json=REPLY)

        health = AccountHealth()
        router = make_router(handler, on_event=events.append, account_health=health)
        for _ in range(2):
            upstream = await router.open(PAYLOAD)
            assert json.loads(await upstream.response.aread()) == REPLY
            await upstream.aclose()
        assert [x[0] for x in calls] == ["wingman.example", "own.example", "own.example"]
        assert "chat_template_kwargs" not in calls[0][1]
        assert calls[0][1]["temperature"] == 0.3
        assert "temperature" not in calls[1][1]
        assert calls[1][1]["messages"] == PAYLOAD["messages"]
        assert all(x["role"] == "support" for x in events)
        assert events[0]["provider_request_id"] == "hosted_123"
        assert "private" not in json.dumps([router.status(), events])
        # Reset only accounts belonging to this router, not arbitrary shared entries.
        health.exhausted.add(("unrelated.example", "other"))
        router.reset()
        assert health.exhausted == {("unrelated.example", "other")}
        await router.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,body,code",
    [
        (429, {"error": {"message": "Try again"}}, "fallback_disabled_for_error"),
        (503, {"error": {"message": "Backend recovering"}}, "fallback_disabled_for_error"),
        (403, {"error": {"code": "denied"}}, "provider_refused"),
        (200, {"choices": []}, "support_response_invalid"),
    ],
)
def test_hosted_errors_are_not_hidden_or_replayed(status, body, code):
    async def run():
        calls = []

        def handler(request):
            calls.append(request.url.host)
            return httpx.Response(status, json=body)

        router = make_router(handler)
        with pytest.raises(RoutingFailure) as exc:
            await router.open(PAYLOAD)
        assert exc.value.code == code
        assert calls == ["wingman.example"]
        assert not router.status()["routes"][0]["credit_exhausted"]
        await router.aclose()

    asyncio.run(run())


def test_support_local_contract_keeps_admin_and_actions_out():
    async def run():
        calls = []

        def handler(request):
            calls.append(request.url.host)
            return httpx.Response(200, json=REPLY)

        router = make_router(handler)
        app = create_support_app(router)
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50001))
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:4142",
            headers={"Authorization": "Bearer not-needed"},
        ) as client:
            assert (await client.get("/v1/models")).json()["data"][0]["id"] == "local-model"
            assert (await client.get("/status")).status_code == 403
            assert (await client.post("/reset")).status_code == 403
            assert (
                await client.post(
                    "/v1/chat/completions", json=PAYLOAD, headers={"Origin": "https://bad.example"}
                )
            ).status_code == 403
            for extra in (
                {"tools": []},
                {"stream": True},
                {"messages": [{"role": "user", "content": [{"type": "image_url"}]}]},
            ):
                assert (
                    await client.post("/v1/chat/completions", json={**PAYLOAD, **extra})
                ).status_code == 400
            assert not calls
            response = await client.post("/v1/chat/completions", json=PAYLOAD)
            assert response.status_code == 200 and response.json() == REPLY
            assert response.headers["x-wingman-waterfall-role"] == "support"
        await router.aclose()

    asyncio.run(run())


def test_support_protocol_cannot_be_used_for_main_or_jev():
    async def secret(_):
        return None

    with pytest.raises(ValueError):
        ProviderWaterfall(
            [
                RouteSpec(
                    "s",
                    "https://x/api/v1/support/completions",
                    "s",
                    wire_protocol="wingman_support",
                )
            ],
            secret,
        )
    with pytest.raises(ValueError):
        ProviderWaterfall(
            [RouteSpec("s", "https://x/v1/chat/completions", "s", jev_assist=True)],
            secret,
            role="support",
        )
    with pytest.raises(ValueError):
        RouteSpec("s", "https://x/v1/chat/completions", "s", omit_request_fields=["messages"])
