"""FastAPI application factory for the Marshal Control Tower service."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from marshal_api.api.v1.router import api_router
from marshal_api.bridge import init_bridge, shutdown_bridge
from marshal_api.config import Settings, get_settings
from marshal_api.core.errors import register_exception_handlers
from marshal_api.db.session import engine

DESCRIPTION = (
    "Read/write API behind the Marshal supervisor dashboard: yard operations, "
    "remote assistance, quality and rework, the CARLA test lab and async jobs."
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The scaffolding opens no DB connections at startup; Alembic owns the schema.
    settings = get_settings()
    if settings.batch_enabled:
        from marshal_api.services.batch_service import mark_stale_batches
        from marshal_api.services.batch_store import get_batch_store

        await mark_stale_batches(get_batch_store(settings))
    init_bridge()
    if settings.run_plant:
        from marshal_api.plant import get_plant

        await get_plant().start(settings.plant_speed)
    yield
    if settings.run_plant:
        from marshal_api.plant import get_plant

        await get_plant().stop()
    if settings.batch_enabled:
        from marshal_api.services.batch_service import shutdown_batches
        from marshal_api.services.batch_store import get_batch_store

        await shutdown_batches(get_batch_store(settings))
    shutdown_bridge()
    await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=DESCRIPTION,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/", tags=["system"])
    async def root() -> dict[str, str]:
        return {"name": settings.app_name, "version": settings.app_version, "docs": "/docs"}

    return app


app = create_app()
