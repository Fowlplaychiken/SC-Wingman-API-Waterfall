"""Authenticated loopback proxy. Upstream status is known before HTTP headers."""

from __future__ import annotations

import hmac
import json
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from .routing import RoutingFailure


def create_app(router, bearer, *, request_limit_bytes=8 * 1024 * 1024):
    if (
        type(bearer) is not str
        or not bearer
        or type(request_limit_bytes) is not int
        or request_limit_bytes <= 0
    ):
        raise ValueError("Bridge bearer and positive request budget required")

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            await router.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def authenticate(request, call_next):
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied.encode(), ("Bearer " + bearer).encode()):
            return JSONResponse({"error": {"code": "bridge_unauthorized"}}, status_code=401)
        return await call_next(request)

    @app.exception_handler(RoutingFailure)
    async def failed(_request, error):
        return JSONResponse({"error": {"code": error.code}}, status_code=error.status)

    @app.get("/v1/models")
    async def models():
        return {"object": "list", "data": [{"id": "waterfall", "object": "model"}]}

    @app.get("/status")
    async def status():
        return router.status()

    @app.post("/reset")
    async def reset():
        return router.reset()

    @app.get("/providers/{route_id}/models")
    async def discover(route_id: str):
        try:
            return {"models": await router.discover_models(route_id)}
        except ValueError:
            return JSONResponse({"error": {"code": "unknown_route"}}, status_code=404)

    @app.post("/v1/chat/completions")
    async def completion(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > request_limit_bytes:
                return JSONResponse({"error": {"code": "request_too_large"}}, status_code=413)
            raw.extend(chunk)
        try:
            payload = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if type(payload) is not dict or type(payload.get("messages")) is not list:
                raise ValueError()
            if "stream" in payload and type(payload["stream"]) is not bool:
                raise ValueError()
        except (ValueError, UnicodeError):
            return JSONResponse({"error": {"code": "invalid_chat_request"}}, status_code=400)
        upstream = await router.open(payload, bridge_port=request.url.port)

        async def chunks():
            size = 0
            try:
                async for chunk in upstream.response.aiter_raw():
                    size += len(chunk)
                    if size > router.response_limit_bytes:
                        raise RoutingFailure(502, "upstream_response_too_large")
                    yield chunk
            finally:
                await upstream.aclose()

        headers = {"x-wingman-waterfall-route": upstream.route_id}
        for key in ("content-type", "content-encoding"):
            if key in upstream.response.headers:
                headers[key] = upstream.response.headers[key]
        if payload.get("stream"):
            # A later stream failure terminates this response; never try another model.
            return StreamingResponse(
                chunks(),
                status_code=200,
                headers=headers,
                background=BackgroundTask(upstream.aclose),
            )
        try:
            body = bytearray()
            async for chunk in chunks():
                body.extend(chunk)
            return Response(bytes(body), status_code=200, headers=headers)
        except (httpx.HTTPError, TimeoutError):
            return JSONResponse({"error": {"code": "upstream_transport_unknown"}}, status_code=502)

    return app
