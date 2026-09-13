"""SafeDrop operations console.

    uvicorn app.main:app --reload

A single Starlette application: scenario catalogue, telemetry replay, the
agentic contingency workflow, and the operator console that reviews its output.
No flight command is ever emitted.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.requests import Request

from app.config import get_config
from app.web.routes import routes


async def unknown_run(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=404)


@contextlib.asynccontextmanager
async def lifespan(_: Starlette) -> AsyncIterator[None]:
    cfg = get_config()
    print(f"SafeDrop console ready — context mode: {cfg.context.default_mode}, model: {cfg.agents.model}")
    print("SIMULATION ONLY. No flight command is sent to any aircraft.")

    # Inside a container the app can be serving perfectly and still be
    # unreachable, because EXPOSE declares a port without publishing it. Say so
    # here rather than leaving someone staring at a browser error.
    if Path("/.dockerenv").exists():
        print(
            "\n  Running in a container. If the browser cannot reach this, the port is\n"
            "  probably not published — EXPOSE alone does not do it:\n"
            "      docker run -p 8000:8000 --env-file .env safedrop:latest\n"
            "      docker compose up console        (note: `compose run` does NOT publish ports)\n"
        )
    yield


app = Starlette(
    debug=False,
    routes=routes,
    lifespan=lifespan,
    exception_handlers={KeyError: unknown_run, FileNotFoundError: unknown_run},
)
