"""FastAPI application entry point for the Merchant Revenue Autopilot.

The app exposes:

- ``GET /health`` (preserved from the Task 01 foundation),
- the ``/api/v1`` product API (Task 15) whose routes only orchestrate the
  existing deterministic engines,
- Task 21A merchant onboarding + initial CSV ingestion,
- an additive explicit cycle-rollover route used to start another optimization
  cycle without deleting historical results,
- environment-driven CORS for the separately deployed dashboard.

CORS is configured from ``CORS_ALLOWED_ORIGINS``: a comma-separated allow-list,
defaulting to the local dashboard origin. The frontend does not require
credentialed cross-origin requests, so credentials stay disabled even when an
operator explicitly uses a wildcard for non-production diagnostics. There is
no authentication in P0.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.cycle_router import router as cycle_router
from app.api.onboarding_router import router as onboarding_router
from app.api.router import router as api_router
from app.config import get_settings

#: Comma-separated allow-list of dashboard origins allowed to call this API.
CORS_ALLOWED_ORIGINS_ENV_VAR = "CORS_ALLOWED_ORIGINS"

#: Backward-compatible alias used by earlier local environments.
LEGACY_CORS_ORIGINS_ENV_VAR = "CORS_ORIGINS"

#: Local Next.js dashboard.
DEFAULT_CORS_ORIGINS: tuple[str, ...] = ("http://localhost:3000",)

#: Only the verbs the product API actually uses.
CORS_ALLOW_METHODS: tuple[str, ...] = ("GET", "POST", "OPTIONS")
CORS_ALLOW_HEADERS: tuple[str, ...] = ("Accept", "Content-Type")


def parse_cors_origins(raw: str | None) -> list[str]:
    """Parse a comma-separated origin allow-list.

    Blank, whitespace-only, and missing values fall back to the local
    dashboard default so a bare ``uvicorn app.main:app`` works offline.
    Entries are trimmed and de-duplicated while preserving operator order.
    """
    if not raw:
        return list(DEFAULT_CORS_ORIGINS)
    origins: list[str] = []
    for item in raw.split(","):
        origin = item.strip()
        if origin and origin not in origins:
            origins.append(origin)
    return origins or list(DEFAULT_CORS_ORIGINS)


def cors_origins_from_env() -> list[str]:
    raw = os.environ.get(CORS_ALLOWED_ORIGINS_ENV_VAR)
    if raw is None:
        raw = os.environ.get(LEGACY_CORS_ORIGINS_ENV_VAR)
    if raw is None:
        settings = get_settings()
        raw = settings.CORS_ORIGINS or settings.CORS_ALLOWED_ORIGINS
    return parse_cors_origins(raw)


def create_app(*, cors_origins: Iterable[str] | None = None) -> FastAPI:
    """Build the API app. ``cors_origins`` stays injectable for tests."""
    origins: Sequence[str] = (
        list(cors_origins) if cors_origins is not None else cors_origins_from_env()
    )

    app = FastAPI(
        title="Merchant Revenue Autopilot API",
        version="0.1.0",
        description=(
            "Deterministic detection, AI-assisted diagnosis, policy-gated "
            "experiment planning, Razorpay Test Mode execution and "
            "fixed-horizon statistics, exposed as one-step Autopilot "
            "orchestration."
        ),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(origins),
        # No auth cookies or browser credentials are required by the P0
        # dashboard. Keeping credentials disabled also prevents the unsafe
        # wildcard-plus-credentials CORS combination.
        allow_credentials=False,
        allow_methods=list(CORS_ALLOW_METHODS),
        allow_headers=list(CORS_ALLOW_HEADERS),
    )

    app.include_router(api_router)
    app.include_router(cycle_router)
    app.include_router(onboarding_router)

    @app.get("/health", tags=["health"], summary="Liveness probe")
    def read_health() -> dict[str, str]:
        return {"status": "ok", "service": "merchant-revenue-autopilot"}

    return app


app = create_app()
