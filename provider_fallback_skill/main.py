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
from .routing import AccountHealth, ProviderWaterfall, RouteSpec
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
            self.port = listener.getsockname()[1]
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
        self._support_server = self._support_router = None
        self._subscribed = False
        self._lock = asyncio.Lock()
        self._keys = {}
        self._secret_hashes = {}
        self._bridge_name = None
        self._event_generation = 0
        self._core_loop = None
        self._setup_server = None
        self._setup_expiry = None
        self._setup_link = None

    def _settings(self, errors):
        properties = {p.id: p.value for p in self.config.custom_properties}
        enabled = properties.get("main_enabled")
        if enabled is None:
            # Legacy profiles opted in by configuring both a secret and nonempty routes.
            configured = properties.get("routes_json", "[]")
            enabled = bool(properties.get("bridge_secret_name")) and bool(json.loads(configured))
        if type(enabled) is not bool:
            raise ValueError("invalid main setting")
        if not enabled:
            return None, None, []
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

    def _support_settings(self):
        properties = {p.id: p.value for p in self.config.custom_properties}
        enabled = properties.get("support_enabled", False)
        if type(enabled) is not bool:
            raise ValueError("invalid support settings")
        if not enabled:
            return False, None, []
        port = properties.get("support_listen_port", 4142)
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("invalid support port")
        if properties.get("main_enabled", True) and port == properties.get("listen_port"):
            raise ValueError("support port conflicts with main")
        routes = json.loads(properties.get("support_routes_json", "[]"))
        if type(routes) is not list or not routes:
            raise ValueError("missing support routes")
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
        mode = properties.get("support_fallback_mode", "credits_only")
        if mode not in presets:
            raise ValueError("invalid support fallback mode")
        default_fallback = presets[mode]
        specs = [
            RouteSpec(**{**route, "fallback_on": route.get("fallback_on", default_fallback)})
            for route in routes
        ]
        return True, port, specs

    async def validate(self):
        errors = await super().validate()
        try:
            self._settings(errors)
            self._support_settings()
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
            role = safe(event.get("role", "main"))
            route = safe(event.get("route_id"))
            model = safe(event.get("model"))
            if kind == "failure":
                reason = event.get("reason")
                status = event.get("http_status")
                facts = {
                    "role": role,
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
                    failure = router.status().get("last_failure") or {}
                    reason = safe(failure.get("reason"))
                    if failure.get("request_id") != event.get("request_id"):
                        reason = "unknown"
                    request_id = safe(event.get("request_id"))
                    self.log.warning(
                        f"Using {role} fallback {route} (configured model {model}); "
                        f"reason: {reason}; request: {request_id}. Capability and cost may change."
                    )
                elif event.get("previous_route_id"):
                    self.log.info(
                        f"{role} primary route restored: {route} (configured model {model})."
                    )
                else:
                    self.log.info(
                        f"{role} primary route selected: {route} (configured model {model})."
                    )
        except Exception:
            marker = getattr(router, "note_notification_failed", None)
            if marker:
                marker()

    async def _stop(self):
        self._event_generation += 1
        if self._support_server is not None:
            if (
                await asyncio.to_thread(self._support_server.stop) is not True
                or not self._support_server.cleanup_confirmed
            ):
                raise RuntimeError("provider_bridge_cleanup_unknown")
            self._support_server = self._support_router = None
        if self._server is not None:
            if (
                await asyncio.to_thread(self._server.stop) is not True
                or not self._server.cleanup_confirmed
            ):
                raise RuntimeError("provider_bridge_cleanup_unknown")
            self._server = None
        self._router = None

    async def _start(self):
        try:
            from services.token_utils import count_tokens
        except ImportError:
            count_tokens = None
        errors = []
        port, bridge_name, specs = self._settings(errors)
        support_enabled, support_port, support_specs = self._support_settings()
        bridge_key = await self.wingman.secrets.retrieve(bridge_name, errors) if specs else None
        keys = {}
        for spec in (*specs, *support_specs):
            if spec.secret_name and spec.secret_name not in keys:
                keys[spec.secret_name] = await self.wingman.secrets.retrieve(spec.secret_name, [])
        if errors or (specs and (not isinstance(bridge_key, str) or not bridge_key)):
            raise RuntimeError("provider_bridge_credentials_unavailable")
        # Missing optional provider secrets can fall through to another configured route.
        self._keys = keys
        self._bridge_name = bridge_name
        self._secret_hashes = {
            name: hashlib.sha256(value.encode()).digest()
            for name, value in {
                **keys,
                **({bridge_name: bridge_key} if bridge_name else {}),
            }.items()
            if isinstance(value, str)
        }

        async def resolve(name):
            return self._keys.get(name)

        self._event_generation += 1
        generation = self._event_generation
        holder = {}
        account_health = AccountHealth()
        if specs:
            self._router = ProviderWaterfall(
                tuple(specs),
                resolve,
                assistance=NativeJevAssist(getattr(self.wingman, "system_one", None)),
                account_health=account_health,
                token_counter=count_tokens,
                on_event=lambda event: self._on_event(event, generation, holder["router"]),
            )
            holder["router"] = self._router
            self._server = OwnedServer(create_app(self._router, bridge_key), port)
            await asyncio.to_thread(self._server.start)
        if support_enabled:
            from .server import create_support_app

            support_holder = {}
            self._support_router = ProviderWaterfall(
                tuple(support_specs),
                resolve,
                role="support",
                account_health=account_health,
                token_counter=count_tokens,
                on_event=lambda event: self._on_event(event, generation, support_holder["router"]),
            )
            support_holder["router"] = self._support_router
            self._support_server = OwnedServer(
                create_support_app(self._support_router), support_port
            )
            try:
                await asyncio.to_thread(self._support_server.start)
            except BaseException:
                await self._stop()
                raise

    async def prepare(self):
        async with self._lock:
            self._core_loop = asyncio.get_running_loop()
            if self.is_unloaded:
                raise RuntimeError("provider_bridge_unloaded")
            if not self._subscribed:
                await super().prepare()
                self._subscribed = True
            if self._server is not None or self._support_server is not None:
                raise RuntimeError("provider_bridge_already_owned")
            if await self.validate():
                raise RuntimeError("provider_bridge_configuration_unavailable")
            await self._start()

    async def secret_changed(self, secrets):
        async with self._lock:
            if (self._server is None and self._support_server is None) or self.is_unloaded:
                return
            if not isinstance(secrets, dict):
                return
            relevant = {
                self._bridge_name,
                *(
                    r.secret_name
                    for router in (self._router, self._support_router)
                    if router
                    for r in router.routes
                    if r.secret_name
                ),
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
            for router in (self._router, self._support_router):
                if router:
                    for route in router.routes:
                        if route.secret_name in changed:
                            router.reset(route.route_id)

    async def unload(self):
        async with self._lock:
            if self._setup_expiry is not None:
                self._setup_expiry.cancel()
                self._setup_expiry = None
            if self._setup_server is not None:
                if not await asyncio.to_thread(self._setup_server.stop):
                    raise RuntimeError("provider_setup_cleanup_unknown")
                self._setup_server = None
                self._setup_link = None
            await self._stop()
            await super().unload()
            self._subscribed = False
            self._keys.clear()
            self._secret_hashes.clear()

    @tool(
        name="provider_fallback_setup",
        description=(
            "Open provider/model setup. Nothing changes until the user "
            "reviews and applies the choices."
        ),
        summarize=False,
    )
    async def provider_fallback_setup(self) -> str:
        from pathlib import Path

        from .setup_engine import SetupEngine
        from .setup_web import create_setup_app

        async with self._lock:
            if self._setup_server is not None:
                await self.wingman.ui.show_dialog(
                    "Provider fallback setup", f"[Open setup]({self._setup_link})"
                )
                return "The existing setup link is shown again in Wingman's setup dialog."
            import secrets

            import httpx

            # Tool turns may use a temporary event loop. The prepared skill's
            # lifecycle loop owns work which must survive that turn.
            loop = self._core_loop
            if loop is None or loop.is_closed() or not loop.is_running():
                raise RuntimeError("provider_setup_lifecycle_loop_unavailable")
            properties = {p.id: p.value for p in self.config.custom_properties}
            core_port = properties.get("setup_core_port", 49111)

            async def retrieve(name):
                # Called by the setup listener; keep facade work on Core's loop.
                future = asyncio.run_coroutine_threadsafe(
                    self.wingman.secrets.retrieve(name, []), loop
                )
                return await asyncio.wrap_future(future)

            def schedule_reload(directory):
                async def reload():
                    # Let the setup HTTP response finish before Core unloads it.
                    await asyncio.sleep(0.5)
                    try:
                        async with httpx.AsyncClient(trust_env=False, timeout=60) as client:
                            response = await client.post(
                                f"http://127.0.0.1:{core_port}/config", json=directory
                            )
                            response.raise_for_status()
                    except (httpx.HTTPError, ValueError):
                        self.wingman.log.error(
                            "Provider setup saved; Core reload failed. "
                            "Reload the configuration in Wingman."
                        )

                loop.call_soon_threadsafe(lambda: loop.create_task(reload()))

            engine = SetupEngine(
                self.wingman.name,
                Path(self.get_generated_files_dir())
                / (hashlib.sha256(self.wingman.name.encode()).hexdigest() + "-setup-undo.json"),
                retrieve,
                port=core_port,
                schedule_reload=schedule_reload,
            )
            token = secrets.token_urlsafe(40)
            app = create_setup_app(engine, token)
            server = OwnedServer(app, 0)
            await asyncio.to_thread(server.start)
            self._setup_server = server
            self._setup_link = f"http://127.0.0.1:{server.port}/#{token}"

            async def expire():
                while True:
                    await asyncio.sleep(max(1, 900 - (time.monotonic() - engine.last_activity)))
                    if (
                        not engine.active_requests
                        and time.monotonic() - engine.last_activity >= 900
                    ):
                        break
                async with self._lock:
                    if self._setup_server is server:
                        if not await asyncio.to_thread(server.stop):
                            raise RuntimeError("provider_setup_cleanup_unknown")
                        self._setup_server = None
                        self._setup_link = None
                        self._setup_expiry = None

            self._setup_expiry = asyncio.run_coroutine_threadsafe(expire(), loop)
            try:
                await self.wingman.ui.show_dialog(
                    "Provider fallback setup",
                    f"[Open setup]({self._setup_link})\n\n"
                    "Choose providers, models and their order. Nothing changes until you apply. "
                    "The private setup link expires after 15 idle minutes; "
                    "close this page when done.",
                )
            except BaseException:
                self._setup_expiry.cancel()
                self._setup_expiry = None
                await asyncio.to_thread(server.stop)
                self._setup_server = None
                self._setup_link = None
                raise
            # The capability link is delivered only to the user's dialog, never
            # through the conversation/model or diagnostic log.
            return (
                "Open the setup link in Wingman's Provider fallback setup dialog. "
                "The link expires after 15 idle minutes."
            )

    @tool(name="provider_fallback_status", description="Show provider fallback route status.")
    async def provider_fallback_status(self, role: str = "main") -> str:
        router = self._router_for(role)
        if not router:
            return "Provider fallback is stopped."
        return json.dumps(
            {
                **router.status(),
                "role": role,
                "enabled": True,
                "payment_notice": "Credentialled provider routes may incur charges.",
            }
        )

    @tool(name="provider_fallback_reset", description="Reset exhausted provider routes.")
    async def provider_fallback_reset(self, role: str = "main") -> str:
        router = self._router_for(role)
        if not router:
            return "Provider fallback is stopped."
        router.reset()
        return "Provider fallback exhaustion state reset."

    @tool(name="provider_fallback_models", description="List models offered by a configured route.")
    async def provider_fallback_models(self, route_id: str, role: str = "main") -> str:
        router = self._router_for(role)
        if not router:
            return "Provider fallback is stopped."
        try:
            return json.dumps(await router.discover_models(route_id))
        except (ValueError, RuntimeError):
            return "Model list unavailable for this route."

    def _router_for(self, role):
        if role == "main":
            return self._router
        if role == "support":
            return self._support_router
        return None
