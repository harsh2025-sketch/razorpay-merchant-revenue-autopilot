"""Dashboard control surface for starting another optimization cycle.

The existing Task 15 public OpenAPI surface remains frozen. This dashboard-only
control closes a terminal/safely-undeployed cycle through
``app.services.cycles`` and returns the next persisted opportunity to drive, if
one exists. It is intentionally excluded from OpenAPI while remaining a normal
HTTP route used by the product UI.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.router import ERROR_RESPONSES, write_view
from app.db.session import get_db
from app.services import cycles


router = APIRouter(prefix="/api/v1")


@router.post(
    "/merchants/{merchant_id}/autopilot/new-cycle",
    response_model=schemas.OpportunityResponse | None,
    responses=ERROR_RESPONSES,
    include_in_schema=False,
)
def start_new_cycle(
    merchant_id: str,
    db: Session = Depends(get_db),
) -> schemas.OpportunityResponse | None:
    """Start another cycle without deleting historical lifecycle state.

    In-progress cycles cannot be skipped. Completed/rejected cycles are
    preserved; an approved cycle with no created treatment resource can be
    explicitly abandoned and cancelled before rollover. The next
    already-detected opportunity is preferred, otherwise the existing
    deterministic detector runs again.
    """

    def build() -> schemas.OpportunityResponse | None:
        opportunity = cycles.start_new_cycle(db, merchant_id)
        if opportunity is None:
            return None
        return schemas.OpportunityResponse.model_validate(opportunity)

    return write_view(db, build)


__all__ = ["router"]
