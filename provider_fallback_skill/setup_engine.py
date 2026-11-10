"""User-invoked setup using Wingman's existing local configuration API."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import secrets
import time
from pathlib import Path

import httpx

from .routing import ProviderWaterfall, RouteSpec

MODULE = "skills.provider_fallback.main"
MODES = {"credits_only", "availability", "manual"}
ROLE_KEYS = {
    "main": ("main_enabled", "routes_json", "fallback_mode"),
    "support": ("support_enabled", "support_routes_json", "support_fallback_mode"),
}


class SetupError(ValueError):
    """Fixed public error codes, never exception bodies or credentials."""


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class SetupEngine:
    def __init__(
        self,
        wingman_name,
        backup_path,
        resolve_secret,
        *,
        port=49111,
        client_factory=httpx.AsyncClient,
        schedule_reload=None,
    ):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise SetupError("invalid_core_port")
        self.name = wingman_name
        self.backup_path = Path(backup_path)
        self._backup_base = self.backup_path
        self._directory_key = None
        self.resolve_secret = resolve_secret
        self.client_factory = client_factory
        self.base = f"http://127.0.0.1:{port}"
        self.schedule_reload = schedule_reload
        self._preview = None
        self._lock = asyncio.Lock()
        self.last_activity = time.monotonic()
        self.active_requests = 0

    async def _request(self, method, path, **kwargs):
        async with self.client_factory(
            base_url=self.base, timeout=30, trust_env=False, follow_redirects=False
        ) as client:
            result = await client.request(method, path, **kwargs)
            if result.status_code >= 300:
                raise SetupError("core_request_failed")
            return result.json()

    async def _read(self):
        directories = await self._request("GET", "/configs")
        directory = directories["current_config_dir"]
        key = directory["directory"]
        if self._directory_key is None:
            self._directory_key = key
            suffix = hashlib.sha256(key.encode()).hexdigest()[:16]
            self.backup_path = self._backup_base.with_name(
                self._backup_base.stem + "-" + suffix + ".json"
            )
        elif self._directory_key != key:
            raise SetupError("configuration_switched_reopen_setup")
        current = await self._request("GET", "/config", params={"config_name": directory["name"]})
        profile = current["config"]["wingmen"][self.name]
        files = await self._request(
            "GET", "/config/wingmen", params={"config_name": directory["name"]}
        )
        info = next(x for x in files if x["name"] == self.name and not x["is_deleted"])
        skill = next(x for x in profile["skills"] if x["module"] == MODULE)
        settings = await self._request("GET", "/settings")
        return {
            "directory": directory,
            "file": info,
            "profile": profile,
            "skill": skill,
            "settings": settings,
        }

    @staticmethod
    def _values(state):
        return {p["id"]: p["value"] for p in state["skill"]["custom_properties"]}

    async def snapshot(self):
        state = await self._read()
        values = self._values(state)
        answer = {"wingman": self.name, "notify_switches": values.get("notify_switches", True)}
        for role, (enabled, routes, mode) in ROLE_KEYS.items():
            answer[role] = {
                "enabled": values.get(enabled, False),
                "mode": values.get(mode, "credits_only"),
                "routes": json.loads(values.get(routes, "[]")),
            }
        answer["undo_available"] = self.backup_path.is_file()
        # Return names only. Values never enter the setup page or its responses.
        secret_map = await self._request("GET", "/secrets")
        answer["secret_names"] = sorted(
            k for k, v in secret_map.items() if isinstance(k, str) and v
        )
        del secret_map
        return answer

    @staticmethod
    def _plan(plan):
        if type(plan) is not dict or type(plan.get("notify_switches")) is not bool:
            raise SetupError("invalid_plan")
        result = {"notify_switches": plan["notify_switches"]}
        for role in ROLE_KEYS:
            item = plan.get(role)
            if type(item) is not dict or type(item.get("enabled")) is not bool:
                raise SetupError("invalid_plan")
            if not item["enabled"]:
                result[role] = {"enabled": False}
                continue
            if (
                item.get("mode") not in MODES
                or type(item.get("routes")) is not list
                or not item["routes"]
            ):
                raise SetupError("choose_provider_and_model")
            ids = set()
            specs = []
            for raw in item["routes"]:
                try:
                    route = RouteSpec(**raw)
                except (ValueError, TypeError):
                    raise SetupError("invalid_provider_route") from None
                if route.route_id in ids or (
                    role == "main" and route.wire_protocol == "wingman_support"
                ):
                    raise SetupError("invalid_provider_route")
                if role == "support" and route.jev_assist:
                    raise SetupError("support_does_not_use_jev")
                ids.add(route.route_id)
                specs.append(route)
            try:
                # Reuse the runtime's whole-chain validation. Construction
                # opens no listener or HTTP client and resolves no secrets.
                ProviderWaterfall(specs, None, role=role)
            except ValueError:
                raise SetupError("invalid_provider_route") from None
            result[role] = copy.deepcopy(item)
        if not any(result[r]["enabled"] for r in ROLE_KEYS):
            raise SetupError("choose_a_role")
        return result

    async def models(self, raw):
        if type(raw) is not dict:
            raise SetupError("invalid_provider_route")
        raw = {
            **raw,
            "route_id": raw.get("route_id") or "model-list",
            "model": raw.get("model") or "model-list",
        }
        route = RouteSpec(**raw)
        if route.wire_protocol == "wingman_support":
            raise SetupError("use_wingman_hosted_model_catalog")
        router = ProviderWaterfall((route,), self.resolve_secret)
        try:
            result = await router.discover_models(route.route_id)
            return (
                [x["id"] if isinstance(x, dict) else x for x in result.get("data", [])]
                if isinstance(result, dict)
                else result
            )
        finally:
            await router.aclose()

    async def preview(self, plan):
        plan = self._plan(plan)
        state = await self._read()
        if plan["support"]["enabled"]:
            # Core SERVER mode also selects the remote embedding endpoint. Do
            # not change that endpoint/model or silently stop local embeddings.
            if state["settings"]["llama_cpp"]["mode"] != "server":
                raise SetupError("configure_remote_embeddings_in_wingman_first")
        identity = secrets.token_urlsafe(24)
        self._preview = (identity, _digest(self._guard(state, plan)), plan)
        changes = []
        for role in ROLE_KEYS:
            if plan[role]["enabled"]:
                changes.append(
                    {
                        "role": role,
                        "order": [
                            {
                                "route": r["route_id"],
                                "model": r["model"],
                                "endpoint": r["endpoint"],
                                "secret_name": r.get("secret_name"),
                                **(
                                    {
                                        "context_window": r["context_window"],
                                        "max_output_tokens": r.get("max_output_tokens", 4096),
                                        "tool_reserve_tokens": r.get("tool_reserve_tokens", 8192),
                                    }
                                    if r.get("context_window") is not None
                                    else {}
                                ),
                            }
                            for r in plan[role]["routes"]
                        ],
                        "fallback": plan[role]["mode"],
                        **(
                            {"core_conversation_model": self._main_model(plan)}
                            if role == "main" else {}
                        ),
                    }
                )
        return {
            "preview_id": identity,
            "changes": changes,
            "notice": (
                "Only selected model routes change. API calls may cost money; "
                "fallback can change capability and cost. Tools, speech, Jev, "
                "scheduling and embedding identity stay unchanged."
            ),
        }

    @staticmethod
    def _main_model(plan):
        first = plan["main"]["routes"][0]
        return first["model"] if first.get("context_window") is not None else "waterfall"

    @staticmethod
    def _guard(state, plan):
        roles = [role for role in ROLE_KEYS if plan[role]["enabled"]]
        values = SetupEngine._values(state)
        return {
            "directory": state["directory"]["directory"],
            "wingman": state["profile"]["name"],
            "fields": SetupEngine._fields(state, roles),
            "ports": {
                role: values.get("listen_port" if role == "main" else "support_listen_port")
                for role in roles
            },
        }

    @staticmethod
    def _fields(state, roles):
        values = SetupEngine._values(state)
        keys = ["notify_switches"]
        for role in roles:
            keys.extend(ROLE_KEYS[role])
        if "main" in roles:
            keys.append("bridge_secret_name")
        return {
            "values": {k: values.get(k) for k in keys},
            "main": {
                "provider": state["profile"]["features"]["conversation_provider"],
                "local_llm": copy.deepcopy(state["profile"].get("local_llm")),
            }
            if "main" in roles
            else None,
            "support": {
                k: state["settings"]["llama_cpp"][k]
                for k in ("mode", "support_remote_host", "support_remote_port")
            }
            if "support" in roles
            else None,
        }

    def _backup(self, data):
        self.backup_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.backup_path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.backup_path)

    @staticmethod
    def _put(state, fields):
        props = state["skill"]["custom_properties"]
        found = {p["id"] for p in props}
        if set(fields["values"]) - found:
            raise SetupError("upgrade_skill_catalog_first")
        for p in props:
            if p["id"] in fields["values"]:
                p["value"] = fields["values"][p["id"]]
        if fields["main"] is not None:
            state["profile"]["features"]["conversation_provider"] = fields["main"]["provider"]
            state["profile"]["local_llm"] = fields["main"]["local_llm"]
        if fields["support"] is not None:
            state["settings"]["llama_cpp"].update(fields["support"])

    async def _save(self, state, support):
        result = await self._request(
            "POST",
            "/config/save-wingman",
            params={"silent": True},
            json={
                "config_dir": state["directory"],
                "wingman_file": state["file"],
                "wingman_config": state["profile"],
            },
        )
        if result is not True:
            raise SetupError("profile_not_saved")
        if support:
            # Re-read after the profile request rather than replay a stale
            # full settings object over an unrelated speech/device change.
            current = await self._request("GET", "/settings")
            wanted = state["settings"]["llama_cpp"]
            for key in ("mode", "support_remote_host", "support_remote_port"):
                current["llama_cpp"][key] = wanted[key]
            await self._request("POST", "/settings", json=current)

    async def apply(self, raw):
        async with self._lock:
            plan = self._plan(raw)
            state = await self._read()
            reviewed = self._preview
            if (
                not reviewed
                or raw.get("preview_id") != reviewed[0]
                or plan != reviewed[2]
                or _digest(self._guard(state, plan)) != reviewed[1]
            ):
                raise SetupError("preview_again_settings_changed")
            self._preview = None  # No replay of a possibly partially saved operation.
            roles = [r for r in ROLE_KEYS if plan[r]["enabled"]]
            before = self._fields(state, roles)
            after = copy.deepcopy(before)
            after["values"]["notify_switches"] = plan["notify_switches"]
            values = self._values(state)
            for role in roles:
                enabled, routes, mode = ROLE_KEYS[role]
                after["values"].update(
                    {
                        enabled: True,
                        routes: json.dumps(plan[role]["routes"]),
                        mode: plan[role]["mode"],
                    }
                )
            if "main" in roles:
                # Core's local provider has a fixed secret name. Reuse an
                # existing value; never replace another Wingman's credential.
                all_secrets = await self._request("GET", "/secrets")
                present = bool(all_secrets.get("local_llm"))
                del all_secrets
                if not present:
                    bearer = secrets.token_urlsafe(40)
                    await self._request("POST", "/secrets", json={"local_llm": bearer})
                    del bearer
                after["values"]["bridge_secret_name"] = "local_llm"
                after["main"] = {
                    "provider": "local_llm",
                    "local_llm": {
                        "endpoint": f"http://127.0.0.1:{values.get('listen_port', 4141)}/v1",
                        "conversation_model": self._main_model(plan),
                    },
                }
            if "support" in roles:
                after["support"].update(
                    mode="server",
                    support_remote_host="http://127.0.0.1",
                    support_remote_port=values.get("support_listen_port", 4142),
                )
            backup = {
                "wingman": self.name,
                "directory": state["directory"],
                "roles": roles,
                "before": before,
                "after": after,
                "state": "saving",
            }
            self._backup(backup)
            self._put(state, after)
            await self._save(state, "support" in roles)
            actual = await self._read()
            if self._fields(actual, roles) != after:
                raise SetupError("saved_settings_differ_check_status")
            backup["state"] = "saved"
            self._backup(backup)
            return {
                "saved": True,
                "activation_needed": True,
                "message": (
                    "Saved. Activate saved setup to reload Wingman's providers. "
                    "No provider benchmark or billable test was run."
                ),
            }

    async def undo(self):
        async with self._lock:
            if not self.backup_path.is_file():
                raise SetupError("no_saved_setup_to_undo")
            backup = json.loads(self.backup_path.read_text(encoding="utf-8"))
            state = await self._read()
            actual = self._fields(state, backup["roles"])
            expected = actual == backup["after"]
            if backup.get("state") == "saving":
                # An explicit Undo may recover a partially saved operation.
                # Never retry the uncertain save, and reject newer values.
                expected = all(
                    actual[k] in (backup["before"][k], backup["after"][k])
                    for k in ("main", "support")
                ) and all(
                    actual["values"][k]
                    in (backup["before"]["values"][k], backup["after"]["values"][k])
                    for k in actual["values"]
                )
            if (
                backup["wingman"] != self.name
                or backup["directory"]["directory"] != state["directory"]["directory"]
                or not expected
            ):
                raise SetupError("undo_would_overwrite_newer_settings")
            self._put(state, backup["before"])
            await self._save(state, "support" in backup["roles"])
            actual = await self._read()
            if self._fields(actual, backup["roles"]) != backup["before"]:
                raise SetupError("saved_settings_differ_check_status")
            backup["state"] = "undone"
            self._backup(backup)
            return {
                "restored": True,
                "activation_needed": True,
                "message": (
                    "Prior provider settings restored. Activate to reload. "
                    "Existing secrets are retained."
                ),
            }

    async def activate(self):
        state = await self._read()
        if self.schedule_reload is None:
            raise SetupError("reload_unavailable")
        self.schedule_reload(state["directory"])
        return {
            "reload_requested": True,
            "message": (
                "Wingman will reload this configuration; this setup page "
                "will close its connection. Reopen setup from Wingman if needed."
            ),
        }
