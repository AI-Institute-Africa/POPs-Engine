from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.alerts import router as alerts_router
from app.api.fraud import router as fraud_router
from app.api.guardrails import router as guardrails_router
from app.api.routes import router
from app.core import observability
from app.core.config import get_settings, validate_for_startup


def create_app() -> FastAPI:
    settings = get_settings()
    validate_for_startup(settings)
    observability.configure_logging(settings)
    app = FastAPI(
        title="POPs Engine",
        version="0.2.0",
        description="Proof-of-payment verification for Cognify. Review-only: no auto-approval.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id", "Retry-After"],
    )
    observability.install(app)
    observability.init_optional(settings, app)
    app.include_router(router)
    app.include_router(guardrails_router)
    app.include_router(fraud_router)
    app.include_router(alerts_router)

    @app.get("/health", tags=["ops"])
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
