"""Offline setup engine and local API tests; no provider or real Core calls."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest
from provider_fallback_skill.setup_engine import SetupEngine, SetupError
from provider_fallback_skill.setup_web import create_setup_app
from yaml import safe_load

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "provider_fallback_skill/default_config.yaml"


def _plan(*, main=False, support=False):
    result = {
        "notify_switches": True,
        "main": {"enabled": main},
        "support": {"enabled": support},
    }
    for role, enabled in (("main", main), ("support", support)):
        if enabled:
            result[role].update(
                mode="credits_only",
                routes=[
                    {
                        "route_id": f"{role}-primary",
                        "endpoint": "https://provider.example/v1/chat/completions",
                        "model": "fixture-model",
                        "secret_name": "fixture-provider-key",
                    }
                ],
            )
    return result


class FakeCore:
    """Small fake of only the Core REST routes used by SetupEngine."""

    def __init__(self, *, secret_present=True, support_mode="server"):
        catalog = safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        properties = copy.deepcopy(catalog["custom_properties"])
        defaults = {prop["id"]: prop["value"] for prop in properties}
        defaults.update({"listen_port": 48141, "support_listen_port": 48142})
        for prop in properties:
            prop["value"] = defaults[prop["id"]]
        self.skill = {"module": catalog["module"], "custom_properties": properties}
        self.profile = {
            "name": "Test Wingman",
            "skills": [self.skill],
            "features": {
                "conversation_provider": "openrouter",
                "speech_provider": "stock-speech",
                "jev_enabled": True,
                "tools": ["search", "trade"],
            },
            "local_llm": {"endpoint": "http://127.0.0.1:11434/v1", "conversation_model": "old"},
            "embeddings": {"provider": "stock-embed", "model": "embed-v1"},
            "other": {"kept": True},
        }
        self.other_profile = {
            "name": "Other",
            "skills": [],
            "features": {"speech_provider": "other"},
        }
        self.directory = {
            "name": "default",
            "directory": "default",
            "is_default": False,
            "is_deleted": False,
        }
        self.file = {
            "name": "Test Wingman",
            "path": "C:/fake/config/test.yaml",
            "is_deleted": False,
        }
        self.settings = {
            "llama_cpp": {
                "mode": support_mode,
                "support_remote_host": "http://old-support-host",
                "support_remote_port": 4000,
                "embeddings_provider": "stock-embed",
                "embeddings_model": "embed-v1",
            },
            "unrelated": {"keep": "settings"},
        }
        self.secrets = {"local_llm": "existing-secret"} if secret_present else {}
        self.calls = []
        self.profile_saves = 0
        self.settings_saves = 0

    async def handle(self, request):
        body = await request.aread()
        payload = json.loads(body) if body else None
        self.calls.append((request.method, request.url.path, payload))
        path = request.url.path
        if request.method == "GET" and path == "/configs":
            return httpx.Response(200, json={"current_config_dir": copy.deepcopy(self.directory)})
        if request.method == "GET" and path == "/config":
            return httpx.Response(
                200,
                json={
                    "config": {
                        "wingmen": {
                            "Test Wingman": copy.deepcopy(self.profile),
                            "Other": copy.deepcopy(self.other_profile),
                        }
                    }
                },
            )
        if request.method == "GET" and path == "/config/wingmen":
            return httpx.Response(200, json=[copy.deepcopy(self.file)])
        if request.method == "GET" and path == "/settings":
            return httpx.Response(200, json=copy.deepcopy(self.settings))
        if request.method == "GET" and path == "/secrets":
            return httpx.Response(200, json=copy.deepcopy(self.secrets))
        if request.method == "POST" and path == "/config/save-wingman":
            self.profile = copy.deepcopy(payload["wingman_config"])
            self.skill = self.profile["skills"][0]
            self.profile_saves += 1
            return httpx.Response(200, json=True)
        if request.method == "POST" and path == "/settings":
            self.settings = copy.deepcopy(payload)
            self.settings_saves += 1
            return httpx.Response(200, json=True)
        if request.method == "POST" and path == "/secrets":
            self.secrets.update(payload)
            return httpx.Response(200, json=True)
        return httpx.Response(404, json={"error": "not_found"})

    def client_factory(self, **kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle), **kwargs)

    def engine(self, backup_path, *, schedule_reload=None):
        async def resolve_secret(_name):
            return None

        return SetupEngine(
            "Test Wingman",
            backup_path,
            resolve_secret,
            port=49111,
            client_factory=self.client_factory,
            schedule_reload=schedule_reload,
        )


def _run(coro):
    return asyncio.run(coro)


def _saved_value(core, property_id):
    return next(p["value"] for p in core.skill["custom_properties"] if p["id"] == property_id)


def test_fresh_setup_is_inactive_and_does_not_mutate_before_preview_and_apply(tmp_path):
    async def scenario():
        core = FakeCore()
        engine = core.engine(tmp_path / "backup.json")
        initial_profile = copy.deepcopy(core.profile)
        initial_settings = copy.deepcopy(core.settings)
        state = await engine.snapshot()
        assert state["main"]["enabled"] is False
        assert state["support"]["enabled"] is False
        assert core.profile == initial_profile and core.settings == initial_settings
        assert core.profile_saves == core.settings_saves == 0

        preview = await engine.preview(_plan(main=True))
        assert preview["changes"][0]["role"] == "main"
        assert core.profile == initial_profile and core.settings == initial_settings
        assert core.profile_saves == core.settings_saves == 0

        plan = _plan(main=True)
        plan["preview_id"] = preview["preview_id"]
        result = await engine.apply(plan)
        assert result["saved"] is True and result["activation_needed"] is True
        assert core.profile_saves == 1 and core.settings_saves == 0

    _run(scenario())


def test_main_apply_preserves_unselected_support_and_other_profile_settings(tmp_path):
    async def scenario():
        core = FakeCore()
        original = copy.deepcopy(core.profile)
        other = copy.deepcopy(core.other_profile)
        settings = copy.deepcopy(core.settings)
        engine = core.engine(tmp_path / "backup.json")
        plan = _plan(main=True)
        plan["preview_id"] = (await engine.preview(_plan(main=True)))["preview_id"]
        await engine.apply(plan)

        assert _saved_value(core, "support_enabled") is False
        assert _saved_value(core, "support_routes_json") == "[]"
        assert _saved_value(core, "support_fallback_mode") == "credits_only"
        assert (
            core.profile["features"]["speech_provider"] == original["features"]["speech_provider"]
        )
        assert core.profile["features"]["jev_enabled"] is True
        assert core.profile["features"]["tools"] == ["search", "trade"]
        assert core.profile["embeddings"] == original["embeddings"]
        assert core.other_profile == other
        assert core.settings == settings
        assert core.profile["local_llm"] == {
            "endpoint": "http://127.0.0.1:48141/v1",
            "conversation_model": "waterfall",
        }

    _run(scenario())


def test_stale_preview_rejects_without_any_write(tmp_path):
    async def scenario():
        core = FakeCore()
        engine = core.engine(tmp_path / "backup.json")
        plan = _plan(main=True)
        preview = await engine.preview(plan)
        core.profile["local_llm"]["endpoint"] = "http://127.0.0.1:9001/v1"
        writes_before = [call for call in core.calls if call[0] == "POST"]
        plan["preview_id"] = preview["preview_id"]
        with pytest.raises(SetupError, match="preview_again_settings_changed"):
            await engine.apply(plan)
        assert [call for call in core.calls if call[0] == "POST"] == writes_before
        assert core.profile_saves == core.settings_saves == 0

    _run(scenario())


def test_unrelated_changes_between_preview_and_apply_are_preserved(tmp_path):
    async def scenario():
        core = FakeCore()
        engine = core.engine(tmp_path / "backup.json")
        plan = _plan(main=True)
        plan["preview_id"] = (await engine.preview(plan))["preview_id"]
        core.profile["features"]["speech_provider"] = "changed-after-preview"
        assert (await engine.apply(plan))["saved"]
        assert core.profile["features"]["speech_provider"] == "changed-after-preview"

    _run(scenario())


@pytest.mark.parametrize(
    "route,expected",
    [
        (
            {
                "route_id": "bad id",
                "endpoint": "https://provider.example/v1/chat/completions",
                "model": "m",
            },
            "invalid_provider_route",
        ),
        (
            {
                "route_id": "r",
                "endpoint": "https://provider.example/v1/chat/completions?x=1",
                "model": "m",
            },
            "invalid_provider_route",
        ),
        (
            {
                "route_id": "r",
                "endpoint": "https://provider.example/v1/chat/completions",
                "model": "m",
                "request_overrides": {"tools": []},
            },
            "invalid_provider_route",
        ),
        (
            {
                "route_id": "r",
                "endpoint": "https://provider.example/v1/support/completions",
                "model": "m",
                "wire_protocol": "wingman_support",
            },
            "invalid_provider_route",
        ),
    ],
)
def test_route_validation_rejects_invalid_routes(route, expected):
    plan = _plan(main=True)
    plan["main"]["routes"] = [route]
    with pytest.raises(SetupError, match=expected):
        SetupEngine._plan(plan)


@pytest.mark.parametrize("role", ["main", "support"])
@pytest.mark.parametrize("local_last", [False, True])
def test_preview_validates_local_fallback_order_before_any_writes(tmp_path, role, local_last):
    async def scenario():
        core = FakeCore()
        engine = core.engine(tmp_path / "backup.json")
        plan = _plan(**{role: True})
        remote = plan[role]["routes"][0]
        local = {
            "route_id": "local",
            "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
            "model": "local-model",
            "final_local": True,
        }
        plan[role]["routes"] = [remote, local] if local_last else [local, remote]
        if local_last:
            result = await engine.preview(plan)
            assert result["preview_id"]
        else:
            with pytest.raises(SetupError, match="invalid_provider_route"):
                await engine.preview(plan)
        assert not [call for call in core.calls if call[0] == "POST"]
        assert core.profile_saves == core.settings_saves == 0
        assert not engine.backup_path.exists()

    _run(scenario())


def test_main_reuses_existing_local_llm_secret_without_replacing_it(tmp_path):
    async def scenario():
        core = FakeCore(secret_present=True)
        engine = core.engine(tmp_path / "backup.json")
        plan = _plan(main=True)
        plan["preview_id"] = (await engine.preview(_plan(main=True)))["preview_id"]
        await engine.apply(plan)
        assert core.secrets == {"local_llm": "existing-secret"}
        assert not [call for call in core.calls if call[:2] == ("POST", "/secrets")]
        assert _saved_value(core, "bridge_secret_name") == "local_llm"

    _run(scenario())


def test_main_creates_only_missing_local_llm_bridge_secret(tmp_path):
    async def scenario():
        core = FakeCore(secret_present=False)
        engine = core.engine(tmp_path / "backup.json")
        plan = _plan(main=True)
        plan["preview_id"] = (await engine.preview(_plan(main=True)))["preview_id"]
        await engine.apply(plan)
        assert set(core.secrets) == {"local_llm"}
        assert isinstance(core.secrets["local_llm"], str) and len(core.secrets["local_llm"]) >= 40
        assert len([call for call in core.calls if call[:2] == ("POST", "/secrets")]) == 1

    _run(scenario())


def test_support_requires_server_mode_and_preserves_embedding_settings(tmp_path):
    async def scenario():
        unsupported = FakeCore(support_mode="local")
        engine = unsupported.engine(tmp_path / "unsupported.json")
        with pytest.raises(SetupError, match="configure_remote_embeddings_in_wingman_first"):
            await engine.preview(_plan(support=True))
        assert unsupported.profile_saves == unsupported.settings_saves == 0

        core = FakeCore(support_mode="server")
        before_settings = copy.deepcopy(core.settings)
        engine = core.engine(tmp_path / "backup.json")
        plan = _plan(support=True)
        plan["preview_id"] = (await engine.preview(_plan(support=True)))["preview_id"]
        await engine.apply(plan)
        assert core.settings["llama_cpp"]["mode"] == "server"
        assert (
            core.settings["llama_cpp"]["embeddings_provider"]
            == before_settings["llama_cpp"]["embeddings_provider"]
        )
        assert (
            core.settings["llama_cpp"]["embeddings_model"]
            == before_settings["llama_cpp"]["embeddings_model"]
        )
        assert core.settings["llama_cpp"]["support_remote_host"] == "http://127.0.0.1"
        assert core.settings["llama_cpp"]["support_remote_port"] == 48142
        assert core.profile_saves == core.settings_saves == 1

    _run(scenario())


def test_undo_restores_owned_fields_and_keeps_intervening_unrelated_edits(tmp_path):
    async def scenario():
        core = FakeCore()
        engine = core.engine(tmp_path / "backup.json")
        plan = _plan(main=True)
        plan["preview_id"] = (await engine.preview(_plan(main=True)))["preview_id"]
        await engine.apply(plan)
        core.profile["features"]["speech_provider"] = "new-speech"
        core.profile["other"] = {"newer": "profile-edit"}
        core.settings["unrelated"] = {"newer": "settings-edit"}

        result = await engine.undo()
        assert result["restored"] is True and result["activation_needed"] is True
        assert core.profile["features"]["conversation_provider"] == "openrouter"
        assert core.profile["features"]["speech_provider"] == "new-speech"
        assert core.profile["other"] == {"newer": "profile-edit"}
        assert core.settings["unrelated"] == {"newer": "settings-edit"}
        assert _saved_value(core, "main_enabled") is False
        assert core.secrets["local_llm"] == "existing-secret"

    _run(scenario())


def test_undo_refuses_to_overwrite_modified_owned_fields(tmp_path):
    async def scenario():
        core = FakeCore()
        engine = core.engine(tmp_path / "backup.json")
        plan = _plan(main=True)
        plan["preview_id"] = (await engine.preview(_plan(main=True)))["preview_id"]
        await engine.apply(plan)
        next(p for p in core.skill["custom_properties"] if p["id"] == "main_enabled")["value"] = (
            False
        )
        posts_before = [call for call in core.calls if call[0] == "POST"]
        with pytest.raises(SetupError, match="undo_would_overwrite_newer_settings"):
            await engine.undo()
        assert [call for call in core.calls if call[0] == "POST"] == posts_before

    _run(scenario())


def test_setup_api_requires_loopback_token_and_same_origin(tmp_path):
    async def scenario():
        core = FakeCore()
        engine = core.engine(tmp_path / "backup.json")
        app = create_setup_app(engine, "a" * 40)
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            no_auth = await client.get("/api/state")
            wrong_auth = await client.get("/api/state", headers={"Authorization": "Bearer wrong"})
            wrong_origin = await client.get(
                "/api/state",
                headers={"Authorization": "Bearer " + "a" * 40, "Origin": "http://evil.example"},
            )
            ok = await client.get("/api/state", headers={"Authorization": "Bearer " + "a" * 40})
        assert no_auth.status_code == wrong_auth.status_code == 401
        assert wrong_origin.status_code == 403
        assert ok.status_code == 200 and ok.headers["cache-control"] == "no-store"

        remote = httpx.ASGITransport(app=app, client=("192.0.2.9", 50000))
        async with httpx.AsyncClient(transport=remote, base_url="http://127.0.0.1") as client:
            response = await client.get(
                "/api/state", headers={"Authorization": "Bearer " + "a" * 40}
            )
        assert response.status_code == 403

    _run(scenario())


def test_activation_callback_runs_only_after_explicit_activate(tmp_path):
    async def scenario():
        core = FakeCore()
        scheduled = []
        engine = core.engine(tmp_path / "backup.json", schedule_reload=scheduled.append)
        plan = _plan(main=True)
        plan["preview_id"] = (await engine.preview(_plan(main=True)))["preview_id"]
        result = await engine.apply(plan)
        assert result["activation_needed"] is True and scheduled == []
        await engine.activate()
        assert scheduled == [core.directory]

    _run(scenario())


def test_activation_without_callback_reports_unavailable(tmp_path):
    async def scenario():
        core = FakeCore()
        engine = core.engine(tmp_path / "backup.json")
        with pytest.raises(SetupError, match="reload_unavailable"):
            await engine.activate()

    _run(scenario())


def test_same_wingman_name_in_two_channels_keeps_separate_undo(tmp_path):
    async def scenario():
        core = FakeCore()
        first = core.engine(tmp_path / "backup.json")
        await first.snapshot()
        first_path = first.backup_path
        core.directory.update(name="second", directory="second")
        second = core.engine(tmp_path / "backup.json")
        await second.snapshot()
        assert second.backup_path != first_path
        with pytest.raises(SetupError, match="configuration_switched"):
            await first.snapshot()

    _run(scenario())
