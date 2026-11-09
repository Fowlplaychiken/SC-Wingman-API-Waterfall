"""Exercise the actual listener with minimal stand-ins for Wingman's Skill imports."""

import importlib
import sys
import types

import httpx
from provider_fallback_skill.routing import ProviderWaterfall, RouteSpec
from provider_fallback_skill.server import create_app


def test_owned_server_starts_and_closes(monkeypatch):
    api = types.ModuleType("api")
    enums = types.ModuleType("api.enums")
    enums.WingmanInitializationErrorType = object
    interface = types.ModuleType("api.interface")
    interface.WingmanInitializationError = object
    skills = types.ModuleType("skills")
    base = types.ModuleType("skills.skill_base")
    base.Skill = object
    base.tool = lambda **_: lambda function: function
    for name, module in (
        ("api", api),
        ("api.enums", enums),
        ("api.interface", interface),
        ("skills", skills),
        ("skills.skill_base", base),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    main = importlib.import_module("provider_fallback_skill.main")

    async def resolve(_name):
        return None

    router = ProviderWaterfall(
        (RouteSpec("local", "http://127.0.0.1:9000/v1/chat/completions", "fixture"),), resolve
    )
    server = main.OwnedServer(create_app(router, "fixture-bearer"), 0)
    try:
        server.start()
        port = server._socket.getsockname()[1]
        with httpx.Client(trust_env=False, timeout=2) as client:
            response = client.get(
                f"http://127.0.0.1:{port}/v1/models",
                headers={"Authorization": "Bearer fixture-bearer"},
            )
        assert response.status_code == 200
        assert response.json()["data"][0]["id"] == "waterfall"
    finally:
        assert server.stop()
        sys.modules.pop("provider_fallback_skill.main", None)
    assert server.cleanup_confirmed and router.status()["closed"] is True
