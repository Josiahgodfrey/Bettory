"""FastAPI application entry point.

Run locally:  fastapi dev app/main.py
"""

import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import bets, data_health, health, matches, paper, sessions, system
from app.core.settings import BACKEND_DIR, get_settings

OPENAPI_FILE = BACKEND_DIR.parent / "docs" / "api" / "openapi.json"


def create_app() -> FastAPI:
    settings = get_settings()

    application = FastAPI(
        title="Bettory it is time to kill KANJI",
        version="0.1.0",
        # Private system: hide interactive docs outside local development.
        docs_url="/docs" if settings.environment == "local" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.environment == "local" else None,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    application.include_router(health.router)
    application.include_router(data_health.router)
    application.include_router(system.router)
    application.include_router(sessions.router)
    application.include_router(matches.router)
    application.include_router(paper.router)
    application.include_router(bets.router)
    return application


def openapi_json() -> str:
    """The OpenAPI schema as committed in docs/api/openapi.json (stable order; a test checks it is current)."""
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


app = create_app()
