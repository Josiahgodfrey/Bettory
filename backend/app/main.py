"""FastAPI application entry point.

Run locally:  fastapi dev app/main.py
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import bets, data_health, health, jobs, matches, paper, reviews, sessions, system
from app.core.settings import get_settings


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
    application.include_router(jobs.router)
    application.include_router(reviews.router)
    application.include_router(data_health.router)
    application.include_router(system.router)
    application.include_router(sessions.router)
    application.include_router(matches.router)
    application.include_router(paper.router)
    application.include_router(bets.router)
    return application


app = create_app()
