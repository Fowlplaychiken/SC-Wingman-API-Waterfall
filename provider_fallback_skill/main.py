"""Optional Wingman v3 lifecycle for the provider waterfall endpoint."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import socket
import threading
import time

from api.enums import WingmanInitializationErrorType
from api.interface import WingmanInitializationError
from skills.skill_base import Skill, tool

from .jev_assist import NativeJevAssist
from .routing import ProviderWaterfall, RouteSpec
from .server import create_app


class OwnedServer:
    """Owned loopback listener, adapted from the project's speech bridge."""

    def __init__(self, app, port):
        self.app = app
        self.port = port
        self._thread = self._server = self._socket = None

    @property
    def cleanup_confirmed(self):
        return self._thread is None and self._socket is None

    def start(self):
        if not self.cleanup_confirmed:
            raise RuntimeError("provider_bridge_cleanup_unknown")
        import uvicorn

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket = listener
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", self.port))
            listener.listen(8)
            self._server = uvicorn.Server(
                uvicorn.Config(
                    self.app,
                    host="127.0.0.1",
                    port=self.port,
                    interface="asgi3",
                    proxy_headers=False,
                    log_level="warning",
                    access_log=False,
                    ws="none",
                    server_header=False,
                    timeout_graceful_shutdown=3,
                )
            )
            self._thread = threading.Thread(
                target=self._server.run,
                kwargs={"sockets": [listener]},
                name="provider-fallback",
                daemon=True,
            )
            self._thread.start()
            deadline = time.monotonic() + 5
            while not self._server.started:
                if not self._thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("provider_bridge_start_failed")
                time.sleep(0.02)
        except BaseException:
            self.stop()
            raise RuntimeError("provider_bridge_start_failed") from None

    def stop(self):
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None and self._thread.ident is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                return False
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                return False
        self._thread = self._server = self._socket = None
        return True


class ProviderFallback(Skill):
    def __init__(self, config, settings, wingman):
        super().__init__(config=config, settings=settings, wingman=wingman)
        self._server = self._router = None
        self._subscribed = False
        self._lock = asyncio.Lock()
        self._keys = {}
        self._secret_hashes = {}
        self._bridge_name = None
        self._event_generation = 0
        self._core_loop = None

    def _settings(self, errors):
        port = self.retrieve_custom_property_value("listen_port", errors)
        bridge_name = self.retrieve_custom_property_value("bridge_secret_name", errors)
        routes = self.retrieve_custom_property_value("routes_json", errors)
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("invalid port")
        if not isinstance(bridge_name, str) or not bridge_name.strip():
            raise ValueError("missing bridge secret")
        raw = json.loads(routes)
        if type(raw) is not list or not raw:
            raise ValueError("missing routes")
        properties = {p.id: p.value for p in self.config.custom_properties}
        mode = properties.get("fallback_mode", "availability")
        notifications = properties.get("notify_switches", True)
        presets = {
            "credits_only": ("credit_exhausted",),
            "availability": (
                "credit_exhausted",
                "authentication_or_model_unavailable",
                "temporary_unavailable",
                "connection_unavailable",
            ),
            "manual": (),
        }
        if mode not in presets or type(notifications) is not bool:
            raise ValueError("invalid fallback settings")
        specs = [
            RouteSpec(**{**route, "fallback_on": route.get("fallback_on", presets[mode])})
            for route in raw
        ]
        return port, bridge_name, specs

    async def validate(self):
        errors = await super().validate()
        try:
            self._settings(errors)
        except (ValueError, TypeError, KeyError):
            errors.append(
                WingmanInitializationError(
                    wingman_name=self.name,
                    error_type=WingmanInitializationErrorType.INVALID_CONFIG,
                    message="Configure a loopback port, bearer secret name and provider routes.",
                )
            )
        return errors

    def _on_event(self, event, generation, router):
        loop = self._core_loop
        if loop is None or loop.is_closed():
            raise RuntimeError("notification_loop_unavailable")
        loop.call_soon_threadsafe(self._log_event, event, generation, router)

    def _log_event(self, event, generation, router):
        if generation != self._event_generation or self.is_unloaded:
            return
        try:
            # Restrict untrusted configured labels before displaying them.
            def safe(value):
                if isinstance(value, str) and any(
                    isinstance(secret, str) and secret and secret in value
                    for secret in self._keys.values()
                ):
                    return "redacted"
                if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_./:+~-]{1,160}", value):
                    return value
                return "unknown"

            kind = event.get("kind")
            route = safe(event.get("route_id"))
            model = safe(event.get("model"))
            if kind == "failure":
                reason = event.get("reason")
                status = event.get("http_status")
                facts = {
                    "route_id": route,
                    "model": model,
                    "reason": safe(reason),
                    "http_status": status if type(status) is int and 100 <= status <= 599 else None,
                    "request_id": safe(event.get("request_id")),
                    "provider_request_id": safe(event.get("provider_request_id")),
                }
                self.log.warning("Provider route failure: " + json.dumps(facts), server_only=True)
            elif kind == "selection":
                properties = {p.id: p.value for p in self.config.custom_properties}
                if not properties.get("notify_switches", True):
                    return
                if event.get("fallback"):
                    self.log.warning(
                        f"Using fallback {route} (configured model {model}); "
                        "capability and cost may change."
                    )
                elif event.get("previous_route_id"):
                    self.log.info(f"Primary route restored: {route} (configured model {model}).")
                else:
                    self.log.info(f"Primary route selected: {route} (configured model {model}).")
        except Exception:
            marker = getattr(router, "note_notification_failed", None)
            if marker:
                marker()

    async def _stop(self):
        self._event_generation += 1
        if self._server is not None:
            if (
                await asyncio.to_thread(self._server.stop) is not True
                or not self._server.cleanup_confirmed
            ):
                raise RuntimeError("provider_bridge_cleanup_unknown")
            self._server = None
        self._router = None

    async def _start(self):
        errors = []
        port, bridge_name, specs = self._settings(errors)
        bridge_key = await self.wingman.secrets.retrieve(bridge_name, errors)
        keys = {}
        for spec in specs:
            if spec.secret_name and spec.secret_name not in keys:
                keys[spec.secret_name] = await self.wingman.secrets.retrieve(spec.secret_name, [])
        if errors or not isinstance(bridge_key, str) or not bridge_key:
            raise RuntimeError("provider_bridge_credentials_unavailable")
        # Missing optional provider secrets can fall through to another configured route.
        self._keys = keys
        self._bridge_name = bridge_name
        self._secret_hashes = {
            name: hashlib.sha256(value.encode()).digest()
            for name, value in {**keys, bridge_name: bridge_key}.items()
            if isinstance(value, str)
        }

        async def resolve(name):
            return self._keys.get(name)

        self._event_generation += 1
        generation = self._event_generation
        holder = {}
        self._router = ProviderWaterfall(
            tuple(specs),
            resolve,
            assistance=NativeJevAssist(getattr(self.wingman, "system_one", None)),
            on_event=lambda event: self._on_event(event, generation, holder["router"]),
        )
        holder["router"] = self._router
        self._server = OwnedServer(create_app(self._router, bridge_key), port)
        await asyncio.to_thread(self._server.start)

    async def prepare(self):
        async with self._lock:
            self._core_loop = asyncio.get_running_loop()
            if self.is_unloaded:
                raise RuntimeError("provider_bridge_unloaded")
            if not self._subscribed:
                await super().prepare()
                self._subscribed = True
            if self._server is not None:
                raise RuntimeError("provider_bridge_already_owned")
            if await self.validate():
                raise RuntimeError("provider_bridge_configuration_unavailable")
            await self._start()

    async def secret_changed(self, secrets):
        async with self._lock:
            if self._server is None or self.is_unloaded:
                return
            if not isinstance(secrets, dict):
                return
            relevant = {
                self._bridge_name,
                *(r.secret_name for r in self._router.routes if r.secret_name),
            }
            changed = {
                name
                for name in relevant
                if name in secrets
                and hashlib.sha256(str(secrets[name] or "").encode()).digest()
                != self._secret_hashes.get(name)
            }
            if not changed:
                return
            if self._bridge_name in changed:
                await self._stop()
                await self._start()
                return
            for name in changed:
                value = secrets[name] if isinstance(secrets[name], str) else None
                self._keys[name] = value
                self._secret_hashes[name] = hashlib.sha256((value or "").encode()).digest()
            for route in self._router.routes:
                if route.secret_name in changed:
                    self._router.reset(route.route_id)

    async def unload(self):
        async with self._lock:
            await self._stop()
            await super().unload()
            self._subscribed = False
            self._keys.clear()
            self._secret_hashes.clear()

    @tool(name="provider_fallback_status", description="Show provider fallback route status.")
    async def provider_fallback_status(self) -> str:
        if not self._router:
            return "Provider fallback is stopped."
        return json.dumps(self._router.status())

    @tool(name="provider_fallback_reset", description="Reset exhausted provider routes.")
    async def provider_fallback_reset(self) -> str:
        if not self._router:
            return "Provider fallback is stopped."
        self._router.reset()
        return "Provider fallback exhaustion state reset."

    @tool(name="provider_fallback_models", description="List models offered by a configured route.")
    async def provider_fallback_models(self, route_id: str) -> str:
        if not self._router:
            return "Provider fallback is stopped."
        try:
            return json.dumps(await self._router.discover_models(route_id))
        except (ValueError, RuntimeError):
            return "Model list unavailable for this route."
