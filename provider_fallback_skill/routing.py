"""Ordered OpenAI-compatible inference routing; no tools are executed here."""

from __future__ import annotations

import asyncio
import json
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from .native_messages import NativeMessagesError, from_native, to_native


class AccountHealth:
    """Session-only credit state shared by roles using the same account reference."""

    def __init__(self):
        self.lock = threading.RLock()
        self.exhausted = set()


@dataclass(frozen=True)
class RouteSpec:
    route_id: str
    endpoint: str
    model: str
    secret_name: str | None = None
    request_overrides: dict = field(default_factory=dict)
    final_local: bool = False
    wire_protocol: str = "openai_chat"
    jev_assist: bool = False
    jev_min_confidence: float = 0.8
    omit_request_fields: tuple[str, ...] = ()
    fallback_on: tuple[str, ...] = (
        "credit_exhausted",
        "authentication_or_model_unavailable",
        "temporary_unavailable",
        "connection_unavailable",
    )

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
            or self.wire_protocol not in {"openai_chat", "anthropic_messages", "wingman_support"}
            or not url.path.endswith(
                "/messages"
                if self.wire_protocol == "anthropic_messages"
                else "/support/completions"
                if self.wire_protocol == "wingman_support"
                else "/chat/completions"
            )
            or type(self.final_local) is not bool
            or type(self.jev_assist) is not bool
            or type(self.jev_min_confidence) not in (int, float)
            or not math.isfinite(self.jev_min_confidence)
            or not 0 <= self.jev_min_confidence <= 1
            or type(self.model) is not str
            or not self.model.strip()
            or (
                self.secret_name is not None
                and (type(self.secret_name) is not str or not self.secret_name.strip())
            )
            or type(self.request_overrides) is not dict
            or type(self.omit_request_fields) not in (list, tuple)
            or any(type(x) is not str for x in self.omit_request_fields)
            or type(self.fallback_on) not in (list, tuple)
            or any(
                x
                not in {
                    "credit_exhausted",
                    "authentication_or_model_unavailable",
                    "temporary_unavailable",
                    "connection_unavailable",
                }
                for x in self.fallback_on
            )
        ):
            raise ValueError("Invalid provider route or protocol endpoint")
        if set(self.request_overrides) & {"messages", "tools", "tool_choice", "model", "stream"}:
            raise ValueError("Route overrides cannot replace messages, tools, model or stream")
        if set(self.omit_request_fields) - {
            "temperature",
            "top_p",
            "top_k",
            "presence_penalty",
            "frequency_penalty",
            "chat_template_kwargs",
            "repeat_penalty",
            "min_p",
            "seed",
            "stop",
        }:
            raise ValueError("Only provider sampling fields can be omitted")
        json.dumps(self.request_overrides, allow_nan=False)


@dataclass
class Upstream:
    response: httpx.Response
    client: httpx.AsyncClient
    route_id: str
    owner: ProviderWaterfall
    request_id: str = ""
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
    def __init__(self, status: int, code: str, request_id: str | None = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.request_id = request_id


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
        assistance=None,
        on_event=None,
        role="main",
        account_health=None,
    ):
        self.routes = tuple(routes)
        if (
            not self.routes
            or any(type(r) is not RouteSpec for r in self.routes)
            or len({r.route_id for r in self.routes}) != len(self.routes)
            or any(r.final_local for r in self.routes[:-1])
            or role not in {"main", "support"}
            or any(r.wire_protocol == "wingman_support" for r in self.routes)
            and role != "support"
            or role == "support"
            and any(r.jev_assist for r in self.routes)
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
        self.role = role
        self._account_health = account_health if account_health is not None else AccountHealth()
        self._lock = self._account_health.lock
        self._state = {}
        self._exhausted = self._account_health.exhausted
        self._active = {}
        self._closed = False
        self.assistance = assistance
        self.on_event = on_event
        self._last_selected_route = None
        self._last_failure = None

    def note_notification_failed(self):
        with self._lock:
            self._notification_failed = True

    def _failure(self, route, reason, status, *, request_id=None, provider_request_id=None):
        event = {
            "role": self.role,
            "route_id": route.route_id,
            "model": route.model,
            "reason": reason,
            "http_status": status,
            "request_id": request_id,
        }
        if provider_request_id and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", provider_request_id):
            event["provider_request_id"] = provider_request_id
        with self._lock:
            self._last_failure = event
            self._state[route.route_id] = {
                "reason": reason,
                "http_status": status,
                "request_id": request_id,
            }
            if reason not in route.fallback_on and reason in {
                "credit_exhausted",
                "authentication_or_model_unavailable",
            }:
                self._state[route.route_id]["terminal_error_status"] = status
        self._event({"kind": "failure", **event})
        if reason not in route.fallback_on:
            code = (
                "provider_refused"
                if reason == "provider_refused"
                else "fallback_disabled_for_error"
            )
            raise RoutingFailure(status, code, request_id)

    def _terminal(self, route, status, code, request_id):
        event = {
            "role": self.role,
            "route_id": route.route_id,
            "model": route.model,
            "reason": code,
            "http_status": status,
            "request_id": request_id,
        }
        with self._lock:
            self._last_failure = event
        self._event({"kind": "failure", **event})
        return RoutingFailure(status, code, request_id)

    def _event(self, event):
        event = {**event, "role": self.role}
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:
                with self._lock:
                    self._notification_failed = True

    @staticmethod
    def _account(route):
        return (urlsplit(route.endpoint).netloc, route.secret_name or route.route_id)

    def status(self):
        with self._lock:
            return {
                "role": self.role,
                "closed": self._closed,
                "last_selected_route": self._last_selected_route,
                "last_failure": self._last_failure,
                "notification_failed": getattr(self, "_notification_failed", False),
                "routes": [
                    {
                        "route_id": r.route_id,
                        "model": r.model,
                        "final_local": r.final_local,
                        "wire_protocol": r.wire_protocol,
                        "jev_assist": r.jev_assist,
                        "fallback_on": list(r.fallback_on),
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
                self._exhausted.difference_update(self._account(r) for r in self.routes)
                self._last_failure = None
                if self.assistance is not None:
                    self.assistance.reset()
            else:
                route = next((r for r in self.routes if r.route_id == route_id), None)
                if route is None:
                    raise ValueError("Unknown route")
                self._state.pop(route_id, None)
                self._exhausted.discard(self._account(route))
        return self.status()

    async def _headers(self, route):
        headers = {"Accept-Encoding": "identity"}
        if route.wire_protocol == "anthropic_messages":
            headers["anthropic-version"] = "2023-06-01"
        if route.secret_name:
            token = await self.resolve_secret(route.secret_name)
            if not token or type(token) is not str:
                return None
            if route.wire_protocol == "anthropic_messages":
                headers["x-api-key"] = token
            else:
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
        request_id = uuid.uuid4().hex
        for route in self.routes:
            with self._lock:
                if self._closed:
                    raise self._terminal(route, 503, "bridge_closed", request_id)
                state = self._state.get(route.route_id, {})
                if state.get("terminal_error_status"):
                    raise self._terminal(
                        route,
                        state["terminal_error_status"],
                        "fallback_disabled_for_error",
                        request_id,
                    )
                if (
                    self._account(route) in self._exhausted
                    and "credit_exhausted" not in route.fallback_on
                ):
                    raise self._terminal(route, 402, "fallback_disabled_for_error", request_id)
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
                raise self._terminal(route, 400, "recursive_bridge_route", request_id)
            headers = await self._headers(route)
            if headers is None:
                self._failure(
                    route, "authentication_or_model_unavailable", 401, request_id=request_id
                )
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
                selected_payload = {
                    key: value
                    for key, value in payload.items()
                    if key not in route.omit_request_fields
                }
                assistance_status = None
                if route.jev_assist and self.assistance is not None:
                    from .jev_assist import NativeJevRefused

                    try:
                        selected_payload, assistance_status = await self.assistance.assist(
                            selected_payload, route.jev_min_confidence
                        )
                    except NativeJevRefused:
                        raise RoutingFailure(403, "decision_provider_refused") from None
                with self._lock:
                    if self._closed:
                        raise RoutingFailure(503, "bridge_closed")
                if route.wire_protocol == "anthropic_messages":
                    try:
                        binding = [route.endpoint, route.secret_name or route.route_id, route.model]
                        native_payload = dict(selected_payload)
                        native_payload["messages"] = []
                        for message in selected_payload["messages"]:
                            if type(message) is not dict:
                                raise NativeMessagesError("invalid_message")
                            message = dict(message)
                            # A fallback is a fresh provider/model context. Only the
                            # original route can replay its opaque signed blocks.
                            if message.pop("wingman_native_route", None) != binding:
                                message.pop("wingman_native_state", None)
                            native_payload["messages"].append(message)
                        body = to_native(native_payload, route.model, route.request_overrides)
                    except NativeMessagesError as exc:
                        raise RoutingFailure(400, str(exc)) from None
                else:
                    # Provider-specific signed state must not cross to another provider.
                    body = {**selected_payload, **route.request_overrides, "model": route.model}
                    body["messages"] = [
                        {
                            k: v
                            for k, v in message.items()
                            if k not in {"wingman_native_state", "wingman_native_route"}
                        }
                        if isinstance(message, dict)
                        else message
                        for message in body["messages"]
                    ]
                    if route.wire_protocol == "wingman_support":
                        # The stock hosted Support endpoint has a smaller contract
                        # than llama.cpp. Keep messages and supported budgets only.
                        body = {
                            k: v
                            for k, v in body.items()
                            if k
                            in {
                                "messages",
                                "model",
                                "max_tokens",
                                "temperature",
                                "top_p",
                                "presence_penalty",
                            }
                        }
                request = client.build_request("POST", route.endpoint, headers=headers, json=body)
                response = await client.send(request, stream=True)
                status = response.status_code
                if status == 200:
                    if route.wire_protocol == "anthropic_messages":
                        try:
                            document = json.loads(await self._read(response))
                            converted_doc = from_native(document, route.model)
                            converted_doc["choices"][0]["message"]["wingman_native_route"] = binding
                            converted = json.dumps(converted_doc, allow_nan=False).encode("utf-8")
                        except (ValueError, TypeError, KeyError):
                            raise RoutingFailure(502, "native_response_invalid") from None
                        if len(converted) > self.response_limit_bytes:
                            raise RoutingFailure(502, "upstream_response_too_large")
                        await response.aclose()
                        response = httpx.Response(
                            200,
                            stream=httpx.ByteStream(converted),
                            request=request,
                            headers={"content-type": "application/json"},
                        )
                    if self.role == "support":
                        try:
                            raw_reply = await self._read(response)
                            reply = json.loads(raw_reply)
                            choice = reply["choices"][0]
                            if type(choice["message"]["content"]) is not str or choice[
                                "message"
                            ].get("tool_calls"):
                                raise ValueError()
                        except (ValueError, TypeError, KeyError, IndexError):
                            raise RoutingFailure(502, "support_response_invalid") from None
                        await response.aclose()
                        response = httpx.Response(
                            200,
                            stream=httpx.ByteStream(raw_reply),
                            request=request,
                            headers={"content-type": "application/json"},
                        )
                    upstream = Upstream(
                        response, client, route.route_id, self, request_id=request_id
                    )
                    with self._lock:
                        if self._closed:
                            raise RoutingFailure(503, "bridge_closed")
                        self._state[route.route_id] = {"reason": "selected"}
                        if assistance_status is not None:
                            self._state[route.route_id]["jev"] = assistance_status
                        self._active[id(upstream)] = upstream
                        handed_off = True
                        previous = self._last_selected_route
                        self._last_selected_route = route.route_id
                    if previous != route.route_id:
                        self._event(
                            {
                                "kind": "selection",
                                "route_id": route.route_id,
                                "model": route.model,
                                "previous_route_id": previous,
                                "fallback": route is not self.routes[0],
                                "request_id": request_id,
                            }
                        )
                    return upstream
                if status == 504:
                    raise RoutingFailure(504, "upstream_timeout_unknown")
                raw = await self._read(response)
                try:
                    document = json.loads(raw)
                    error = document.get("error", document)
                    code = error.get("code") or error.get("type")
                    message = error.get("message")
                except (ValueError, AttributeError):
                    error = {}
                    code = None
                    message = None
                # Provider content/policy denials are terminal, never routed around.
                if status == 403 or code in {
                    "content_policy_violation",
                    "content_filter",
                    "moderation_blocked",
                }:
                    self._failure(
                        route,
                        "provider_refused",
                        status if status >= 400 else 403,
                        request_id=request_id,
                    )
                anthropic_credit = (
                    url.hostname == "api.anthropic.com"
                    and status == 400
                    and isinstance(error, dict)
                    and error.get("type") == "invalid_request_error"
                    and isinstance(message, str)
                    and message.startswith(
                        "Your credit balance is too low to access the Anthropic API."
                    )
                )
                exhausted = (
                    anthropic_credit
                    or status == 402
                    or code
                    in {
                        "insufficient_quota",
                        "insufficient_credits",
                        "credit_balance_too_low",
                        "support_allowance_exhausted",
                        "allowance_exhausted",
                        "monthly_quota_exceeded",
                    }
                )
                reason = (
                    "credit_exhausted"
                    if exhausted
                    else "authentication_or_model_unavailable"
                    if status in {401, 404}
                    else "temporary_unavailable"
                    if status in {429, 500, 502, 503}
                    or (status == 529 and url.hostname == "api.anthropic.com")
                    else None
                )
                if reason is not None:
                    provider_request_id = response.headers.get(
                        "request-id"
                    ) or response.headers.get("x-request-id")
                    # Do not retain a reflected authentication value as a request ID.
                    if any(
                        value.removeprefix("Bearer ") in (provider_request_id or "")
                        for key, value in headers.items()
                        if key in {"Authorization", "x-api-key"}
                    ):
                        provider_request_id = None
                    self._failure(
                        route,
                        reason,
                        status,
                        request_id=request_id,
                        provider_request_id=provider_request_id,
                    )
                with self._lock:
                    if exhausted:
                        self._exhausted.add(self._account(route))
                        self._state[route.route_id].update(reason="credit_exhausted")
                    elif status in {401, 404}:
                        self._state[route.route_id] = {
                            "reason": "authentication_or_model_unavailable",
                            "disabled_until_reset": True,
                        }
                    elif status in {429, 500, 502, 503} or (
                        status == 529 and url.hostname == "api.anthropic.com"
                    ):
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
            except RoutingFailure as exc:
                if exc.request_id is None:
                    raise self._terminal(route, exc.status, exc.code, request_id) from None
                raise
            except httpx.ConnectError:
                self._failure(route, "connection_unavailable", 503, request_id=request_id)
                with self._lock:
                    self._state[route.route_id] = {
                        "reason": "connection_unavailable",
                        "retry_at": time.monotonic() + self.cooldown_seconds,
                    }
            except httpx.TimeoutException:
                # An ambiguous in-flight timeout is not silently replayed elsewhere.
                raise self._terminal(route, 504, "upstream_timeout_unknown", request_id) from None
            except httpx.HTTPError:
                raise self._terminal(route, 502, "upstream_transport_unknown", request_id) from None
            finally:
                if not handed_off:
                    if response is not None:
                        await response.aclose()
                    await client.aclose()
        raise self._terminal(route, 503, "no_available_provider", request_id)

    async def discover_models(self, route_id):
        route = next((r for r in self.routes if r.route_id == route_id), None)
        if route is None:
            raise ValueError("Unknown route")
        if route.wire_protocol == "wingman_support":
            # Hosted catalogue is owned by Wingman's client/provider integration;
            # do not invent an OpenAI /models endpoint for that protocol.
            raise RoutingFailure(501, "use_wingman_support_model_catalogue")
        headers = await self._headers(route)
        if headers is None:
            raise RoutingFailure(401, "missing_secret")
        if (
            urlsplit(route.endpoint).hostname == "api.anthropic.com"
            and route.wire_protocol == "openai_chat"
        ):
            headers["x-api-key"] = headers.pop("Authorization").removeprefix("Bearer ")
            headers["anthropic-version"] = "2023-06-01"
        suffix = "/messages" if route.wire_protocol == "anthropic_messages" else "/chat/completions"
        url = route.endpoint.removesuffix(suffix) + "/models"
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
