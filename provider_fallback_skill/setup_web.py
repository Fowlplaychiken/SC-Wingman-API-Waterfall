"""Short-lived local setup UI; engine owns all supported configuration actions."""

from __future__ import annotations

import hmac
import ipaddress
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .setup_engine import SetupError


def create_setup_app(engine, token):
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("setup token must be at least 32 characters")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    page = (Path(__file__).parent / "setup.html").read_text(encoding="utf8")

    @app.middleware("http")
    async def protect(request: Request, call_next):
        try:
            client = ipaddress.ip_address(request.client.host)
            host = request.url.hostname
            is_host_local = host == "localhost" or ipaddress.ip_address(host).is_loopback
            origin = request.headers.get("origin")
            if origin:
                parsed = urlsplit(origin)
                if (
                    (parsed.scheme, parsed.hostname, parsed.port)
                    != (request.url.scheme, host, request.url.port)
                    or parsed.username
                    or parsed.password
                    or parsed.path not in ("", "/")
                ):
                    raise ValueError()
            if not client.is_loopback or not is_host_local:
                raise ValueError()
        except (ValueError, AttributeError):
            return JSONResponse({"error": "local_access_required"}, status_code=403)
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("authorization", "")
            if not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        engine.active_requests += 1
        engine.last_activity = time.monotonic()
        try:
            response = await call_next(request)
        finally:
            engine.active_requests -= 1
            engine.last_activity = time.monotonic()
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
        )
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return page

    async def invoke(method, argument=None):
        try:
            if argument is None:
                result = await getattr(engine, method)()
            else:
                result = await getattr(engine, method)(argument)
            return {"result": result}
        except SetupError as error:
            allowed = {
                "choose_a_role",
                "choose_provider_and_model",
                "invalid_provider_route",
                "configure_remote_embeddings_in_wingman_first",
                "preview_again_settings_changed",
                "undo_would_overwrite_newer_settings",
                "core_request_failed",
                "no_saved_setup_to_undo",
            }
            code = str(error) if str(error) in allowed else "setup_unavailable"
            return JSONResponse({"error": code}, status_code=400)
        except Exception:
            return JSONResponse({"error": "setup_unavailable"}, status_code=400)

    @app.get("/api/state")
    async def state():
        return await invoke("snapshot")

    @app.post("/api/models")
    async def models(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return await invoke("models", body)

    @app.post("/api/preview")
    async def preview(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return await invoke("preview", body)

    @app.post("/api/apply")
    async def apply(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return await invoke("apply", body)

    @app.post("/api/activate")
    async def activate():
        return await invoke("activate")

    @app.post("/api/undo")
    async def undo():
        return await invoke("undo")

    return app
