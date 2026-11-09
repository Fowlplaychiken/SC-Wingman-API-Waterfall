"""Ordered OpenAI-compatible inference routing; no tools are executed here."""

from __future__ import annotations

import asyncio
import json
import math
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class RouteSpec:
    route_id: str
    endpoint: str
    model: str
    secret_name: str | None = None
    request_overrides: dict = field(default_factory=dict)
    final_local: bool = False

    def __post_init__(self):
        url = urlsplit(self.endpoint)
        if (
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", self.route_id)
            or url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or not url.path.endswith("/chat/completions")
            or type(self.model) is not str
            or not self.model.strip()
            or (
                self.secret_name is not None
                and (type(self.secret_name) is not str or not self.secret_name.strip())
            )
            or type(self.request_overrides) is not dict
        ):
            raise ValueError("Invalid provider route; use an explicit compatible chat endpoint")
        if set(self.request_overrides) & {"messages", "tools", "tool_choice", "model", "stream"}:
            raise ValueError("Route overrides cannot replace messages, tools, model or stream")
        json.dumps(self.request_overrides, allow_nan=False)


@dataclass
class Upstream:
    response: httpx.Response
    client: httpx.AsyncClient
    route_id: str
    owner: ProviderWaterfall
    closed: bool = False

    async def aclose(self):
        if not self.closed:
            self.closed = True
            try:
                await self.response.aclose()
            finally:
                await self.client.aclose()
                with self.owner._lock:
                    self.owner._active.pop(id(self), None)


class RoutingFailure(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


class ProviderWaterfall:
    def __init__(
        self,
        routes,
        resolve_secret,
        *,
        client_factory=None,
        timeout_seconds=30,
        response_limit_bytes=8 * 1024 * 1024,
        cooldown_seconds=30,
    ):
        self.routes = tuple(routes)
        if (
            not self.routes
            or any(type(r) is not RouteSpec for r in self.routes)
            or len({r.route_id for r in self.routes}) != len(self.routes)
            or any(r.final_local for r in self.routes[:-1])
        ):
            raise ValueError(
                "Unique ordered routes are required; optional local fallback goes last"
            )
        for value in (timeout_seconds, cooldown_seconds):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("Timeout and cooldown must be positive seconds")
        if type(response_limit_bytes) is not int or response_limit_bytes <= 0:
            raise ValueError("Response byte budget must be positive")
        self.resolve_secret = resolve_secret
        self.client_factory = client_factory or httpx.AsyncClient
        self.timeout_seconds = timeout_seconds
        self.response_limit_bytes = response_limit_bytes
        self.cooldown_seconds = cooldown_seconds
        self._lock = threading.RLock()
        self._state = {}
        self._exhausted = set()
        self._active = {}
        self._closed = False

    @staticmethod
    def _account(route):
        return (urlsplit(route.endpoint).netloc, route.secret_name or route.route_id)

    def status(self):
        with self._lock:
            return {
                "closed": self._closed,
                "routes": [
                    {
                        "route_id": r.route_id,
                        "model": r.model,
                        "final_local": r.final_local,
                        "credit_exhausted": self._account(r) in self._exhausted,
                        **self._state.get(r.route_id, {}),
                    }
                    for r in self.routes
                ],
            }

    def reset(self, route_id=None):
        with self._lock:
            if route_id is None:
                self._state.clear()
                self._exhausted.clear()
            else:
                route = next((r for r in self.routes if r.route_id == route_id), None)
                if route is None:
                    raise ValueError("Unknown route")
                self._state.pop(route_id, None)
                self._exhausted.discard(self._account(route))
        return self.status()

    async def _headers(self, route):
        headers = {"Accept-Encoding": "identity"}
        if route.secret_name:
            token = await self.resolve_secret(route.secret_name)
            if not token or type(token) is not str:
                return None
            headers["Authorization"] = "Bearer " + token
        return headers

    async def _read(self, response):
        data = bytearray()
        async for chunk in response.aiter_bytes():
            if len(data) + len(chunk) > self.response_limit_bytes:
                raise RoutingFailure(502, "upstream_response_too_large")
            data.extend(chunk)
        return bytes(data)

    async def open(self, payload, *, bridge_port=None):
        """Inspect upstream status before the caller commits HTTP response headers."""
        for route in self.routes:
            with self._lock:
                if self._closed:
                    raise RoutingFailure(503, "bridge_closed")
                state = self._state.get(route.route_id, {})
                if (
                    self._account(route) in self._exhausted
                    or state.get("disabled_until_reset")
                    or state.get("retry_at", 0) > time.monotonic()
                ):
                    continue
            url = urlsplit(route.endpoint)
            if (
                bridge_port
                and url.hostname in {"localhost", "127.0.0.1", "::1"}
                and url.port == bridge_port
            ):
                raise RoutingFailure(400, "recursive_bridge_route")
            headers = await self._headers(route)
            if headers is None:
                with self._lock:
                    self._state[route.route_id] = {
                        "reason": "missing_secret",
                        "disabled_until_reset": True,
                    }
                continue
            client = self.client_factory(
                trust_env=False, follow_redirects=False, timeout=self.timeout_seconds
            )
            response = None
            handed_off = False
            try:
                with self._lock:
                    if self._closed:
                        raise RoutingFailure(503, "bridge_closed")
                body = {**payload, **route.request_overrides, "model": route.model}
                request = client.build_request("POST", route.endpoint, headers=headers, json=body)
                response = await client.send(request, stream=True)
                status = response.status_code
                if status == 200:
                    upstream = Upstream(response, client, route.route_id, self)
                    with self._lock:
                        if self._closed:
                            raise RoutingFailure(503, "bridge_closed")
                        self._state[route.route_id] = {"reason": "selected"}
                        self._active[id(upstream)] = upstream
                        handed_off = True
                    return upstream
                raw = await self._read(response)
                try:
                    error = json.loads(raw).get("error", {})
                    code = error.get("code") or error.get("type")
                except (ValueError, AttributeError):
                    code = None
                # Provider content/policy denials are terminal, never routed around.
                if status == 403 or code in {
                    "content_policy_violation",
                    "content_filter",
                    "moderation_blocked",
                }:
                    raise RoutingFailure(status if status >= 400 else 403, "provider_refused")
                exhausted = status == 402 or code in {
                    "insufficient_quota",
                    "insufficient_credits",
                    "credit_balance_too_low",
                }
                with self._lock:
                    if exhausted:
                        self._exhausted.add(self._account(route))
                        self._state[route.route_id] = {"reason": "credit_exhausted"}
                    elif status in {401, 404}:
                        self._state[route.route_id] = {
                            "reason": "authentication_or_model_unavailable",
                            "disabled_until_reset": True,
                        }
                    elif status in {429, 500, 502, 503, 504}:
                        retry = response.headers.get("retry-after", "")
                        seconds = (
                            float(retry)
                            if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", retry)
                            else self.cooldown_seconds
                        )
                        self._state[route.route_id] = {
                            "reason": "temporary_unavailable",
                            "retry_at": time.monotonic() + seconds,
                        }
                    else:
                        raise RoutingFailure(status, "upstream_request_rejected")
            except httpx.ConnectError:
                with self._lock:
                    self._state[route.route_id] = {
                        "reason": "connection_unavailable",
                        "retry_at": time.monotonic() + self.cooldown_seconds,
                    }
            except httpx.TimeoutException as exc:
                # An ambiguous in-flight timeout is not silently replayed elsewhere.
                raise RoutingFailure(504, "upstream_timeout_unknown") from exc
            except httpx.HTTPError as exc:
                raise RoutingFailure(502, "upstream_transport_unknown") from exc
            finally:
                if not handed_off:
                    if response is not None:
                        await response.aclose()
                    await client.aclose()
        raise RoutingFailure(503, "no_available_provider")

    async def discover_models(self, route_id):
        route = next((r for r in self.routes if r.route_id == route_id), None)
        if route is None:
            raise ValueError("Unknown route")
        headers = await self._headers(route)
        if headers is None:
            raise RoutingFailure(401, "missing_secret")
        if urlsplit(route.endpoint).hostname == "api.anthropic.com":
            headers["x-api-key"] = headers.pop("Authorization").removeprefix("Bearer ")
            headers["anthropic-version"] = "2023-06-01"
        url = route.endpoint.removesuffix("/chat/completions") + "/models"
        async with self.client_factory(
            trust_env=False, follow_redirects=False, timeout=self.timeout_seconds
        ) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code != 200:
                    raise RoutingFailure(response.status_code, "model_list_unavailable")
                document = json.loads(await self._read(response))
        return sorted(
            {
                row["id"]
                for row in document.get("data", [])
                if type(row) is dict and type(row.get("id")) is str
            }
        )

    async def aclose(self):
        with self._lock:
            self._closed = True
            active = tuple(self._active.values())
        await asyncio.gather(*(item.aclose() for item in active))
