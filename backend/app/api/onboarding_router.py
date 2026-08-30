"""Task 21A merchant onboarding API.

The existing product API remains responsible for optimization lifecycle
transitions. This router only owns merchant registration and merchant data
provenance. Registration + initial CSV ingestion is atomic: an invalid upload
cannot leave an empty merchant behind.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.onboarding_schemas import (
    DemoMerchantResponse,
    MerchantDataStatusResponse,
    OnboardedMerchantResponse,
)
from app.db.models import Merchant
from app.db.session import get_db
from app.services.csv_shape import validate_initial_csv_shape
from app.services.onboarding import (
    MAX_CSV_BYTES,
    TECHBAZAAR_MERCHANT_ID,
    MerchantAlreadyHasDataError,
    MerchantCsvValidationError,
    MerchantOnboardingNotFoundError,
    OnboardingError,
    ingest_initial_csv,
    merchant_data_status,
    register_merchant,
)

logger = logging.getLogger("app.api.onboarding")
router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _safe_onboarding_message(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:300]


def _data_status_response(status) -> MerchantDataStatusResponse:
    return MerchantDataStatusResponse(
        merchant_id=status.merchant_id,
        data_source=status.data_source,
        historical_observations=status.historical_observations,
        real_observations=status.real_observations,
        simulated_observations=status.simulated_observations,
        segment_count=status.segment_count,
        has_data=status.has_data,
    )


@router.get(
    "/demo",
    response_model=DemoMerchantResponse,
    summary="Resolve the canonical TechBazaar demo data source",
)
def read_demo_source(db: Session = Depends(get_db)) -> DemoMerchantResponse:
    merchant = db.get(Merchant, TECHBAZAAR_MERCHANT_ID)
    if merchant is None:
        raise _error(404, "DEMO_NOT_AVAILABLE", "TechBazaar demo data is not available")
    status = merchant_data_status(db, merchant.id)
    return DemoMerchantResponse(
        merchant_id=merchant.id,
        name=merchant.name,
        data_source="demo",
        historical_observations=status.historical_observations,
        segment_count=status.segment_count,
    )


@router.get(
    "/merchants/{merchant_id}/data-status",
    response_model=MerchantDataStatusResponse,
    summary="Read merchant historical-data provenance",
)
def read_merchant_data_status(
    merchant_id: str, db: Session = Depends(get_db)
) -> MerchantDataStatusResponse:
    try:
        return _data_status_response(merchant_data_status(db, merchant_id))
    except MerchantOnboardingNotFoundError as exc:
        raise _error(404, "MERCHANT_NOT_FOUND", _safe_onboarding_message(exc)) from None


@router.post(
    "/merchants/with-csv",
    response_model=OnboardedMerchantResponse,
    status_code=201,
    summary="Register a merchant and ingest its initial canonical payment CSV",
)
async def onboard_merchant_with_csv(
    name: str = Form(...),
    category: str | None = Form(None),
    monthly_gmv_paise: int | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> OnboardedMerchantResponse:
    filename = (file.filename or "").strip().lower()
    if filename and not filename.endswith(".csv"):
        raise _error(422, "CSV_REQUIRED", "Upload must be a .csv file")

    content = await file.read(MAX_CSV_BYTES + 1)

    try:
        # Run shape validation before creating any merchant row. The semantic
        # parser below still validates every field/value before commit.
        validate_initial_csv_shape(content)
        merchant = register_merchant(
            db,
            name=name,
            category=category,
            monthly_gmv_paise=monthly_gmv_paise,
        )
        result = ingest_initial_csv(db, merchant_id=merchant.id, content=content)
        payload = OnboardedMerchantResponse(
            merchant_id=merchant.id,
            name=merchant.name,
            category=merchant.category,
            monthly_gmv_paise=merchant.monthly_gmv,
            created_at=merchant.created_at,
            data_source=result.data_status.data_source,
            rows_imported=result.rows_imported,
            historical_observations=result.data_status.historical_observations,
            real_observations=result.data_status.real_observations,
            simulated_observations=result.data_status.simulated_observations,
            segment_count=result.data_status.segment_count,
        )
        db.commit()
        return payload
    except MerchantAlreadyHasDataError as exc:
        db.rollback()
        raise _error(409, "MERCHANT_ALREADY_HAS_DATA", _safe_onboarding_message(exc)) from None
    except MerchantOnboardingNotFoundError as exc:
        db.rollback()
        raise _error(404, "MERCHANT_NOT_FOUND", _safe_onboarding_message(exc)) from None
    except MerchantCsvValidationError as exc:
        db.rollback()
        raise _error(422, "CSV_VALIDATION_FAILED", _safe_onboarding_message(exc)) from None
    except OnboardingError as exc:
        db.rollback()
        raise _error(422, "ONBOARDING_INVALID", _safe_onboarding_message(exc)) from None
    except Exception:  # noqa: BLE001 - never surface internals to the browser
        db.rollback()
        logger.exception("unexpected merchant onboarding failure")
        raise _error(500, "ONBOARDING_FAILED", "Merchant onboarding could not be completed") from None
