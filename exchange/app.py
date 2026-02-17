from __future__ import annotations

import uvicorn
from fastapi import APIRouter, FastAPI

from exchange.config import engine, settings
from exchange.models import Base
from exchange.routes import accounts, settlement, stats


def create_app() -> FastAPI:
    app = FastAPI(title="A2A Settlement Exchange", version="0.1.0")

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "service": "a2a-settlement-exchange",
            "version": "0.1.0",
        }

    api_router = APIRouter()
    api_router.include_router(accounts.router)
    api_router.include_router(settlement.router)
    api_router.include_router(stats.router)

    # Canonical prefix (spec).
    app.include_router(api_router, prefix="/v1")
    # Compatibility prefix (prototype).
    app.include_router(api_router, prefix="/api/v1")

    @app.on_event("startup")
    def _startup() -> None:
        if settings.auto_create_schema:
            Base.metadata.create_all(bind=engine)

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

