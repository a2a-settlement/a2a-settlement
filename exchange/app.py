from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import APIRouter, FastAPI

from exchange.config import engine, settings
from exchange.middleware import IdempotencyMiddleware, RequestIdMiddleware
from exchange.models import Base
from exchange.routes import accounts, settlement, stats, webhooks
from exchange.schemas import HealthResponse


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    if settings.auto_create_schema:
        Base.metadata.create_all(bind=engine)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="A2A Settlement Exchange",
        version="0.7.0",
        description=(
            "REST API for the A2A Settlement Extension (A2A-SE) exchange service. "
            "Provides escrow-based token settlement for the Agent2Agent protocol."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        openapi_tags=[
            {"name": "Health", "description": "Service health check"},
            {"name": "Accounts", "description": "Agent registration, directory, and account management"},
            {"name": "Settlement", "description": "Escrow, release, refund, dispute, and resolve operations"},
            {"name": "Webhooks", "description": "Webhook registration and management"},
            {"name": "Stats", "description": "Network health and statistics"},
        ],
    )

    app.add_middleware(IdempotencyMiddleware)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/health", response_model=HealthResponse, tags=["Health"])
    def health() -> HealthResponse:
        return HealthResponse()

    api_router = APIRouter()
    api_router.include_router(accounts.router)
    api_router.include_router(settlement.router)
    api_router.include_router(stats.router)
    api_router.include_router(webhooks.router)

    app.include_router(api_router, prefix="/v1")
    app.include_router(api_router, prefix="/api/v1")

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "exchange.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )
