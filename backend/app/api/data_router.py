"""Task 21B data-update API.

These routes are dashboard controls rather than additions to the frozen Task 15
public OpenAPI contract, so they are intentionally excluded from schema output.
They never run opportunity detection themselves: they only append new merchant
evidence. The next Autopilot cycle decides whether a fresh detection pass is
now warranted.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.data_schemas import DemoPeriodResponse, IncrementalCsvResponse
from app.db.session import get_db
from app.services.incremental_data import (
    IncrementalDataConflictError,
    IncrementalDataError,
    IncrementalDataSourceError,
    append_next_demo_period,
    ingest_incremental_csv,
)
from app.services.onboarding import (
    MAX_CSV_BYTES,
    MerchantCsvValidationError,
    MerchantOnboardingNotFoundError,
)

logger = logging.getLogger("app.api.data")
router = APIRouter(prefix="/api/v1/data", tags=["data"])


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _safe_message(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:300]


def _incremental_response(result) -> IncrementalCsvResponse:
    status = result.data_status
    return IncrementalCsvResponse(
        merchant_id=result.merchant_id,
        data_source=status.data_source,
        rows_received=result.rows_received,
        rows_appended=result.rows_appended,
        rows_deduplicated=result.rows_deduplicated,
        historical_observations=status.historical_observations,
        real_observations=status.real_observations,
        simulated_observations=status.simulated_observations,
        segment_count=status.segment_count,
    )


@router.post(
    "/merchants/{merchant_id}/append-csv",
    response_model=IncrementalCsvResponse,
    include_in_schema=False,
)
async def append_merchant_csv(
    merchant_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> IncrementalCsvResponse:
    filename = (file.filename or "").strip().lower()
    if filename and not filename.endswith(".csv"):
        raise _error(422, "CSV_REQUIRED", "Upload must be a .csv file")

    content = await file.read(MAX_CSV_BYTES + 1)
    try:
        result = ingest_incremental_csv(db, merchant_id=merchant_id, content=content)
        payload = _incremental_response(result)
        db.commit()
        return payload
    except MerchantOnboardingNotFoundError as exc:
        db.rollback()
        raise _error(404, "MERCHANT_NOT_FOUND", _safe_message(exc)) from None
    except MerchantCsvValidationError as exc:
        db.rollback()
        raise _error(422, "CSV_VALIDATION_FAILED", _safe_message(exc)) from None
    except IncrementalDataSourceError as exc:
        db.rollback()
        raise _error(409, "DATA_SOURCE_CONFLICT", _safe_message(exc)) from None
    except IncrementalDataConflictError as exc:
        db.rollback()
        raise _error(409, "TRANSACTION_CONFLICT", _safe_message(exc)) from None
    except IncrementalDataError as exc:
        db.rollback()
        raise _error(422, "DATA_UPDATE_INVALID", _safe_message(exc)) from None
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("unexpected incremental CSV failure")
        raise _error(500, "DATA_UPDATE_FAILED", "Data update could not be completed") from None


@router.post(
    "/demo/next-period",
    response_model=DemoPeriodResponse,
    include_in_schema=False,
)
def append_demo_period(db: Session = Depends(get_db)) -> DemoPeriodResponse:
    try:
        result = append_next_demo_period(db)
        status = result.data_status
        payload = DemoPeriodResponse(
            merchant_id=result.merchant_id,
            data_source=status.data_source,
            period_index=result.period_index,
            period_start=result.period_start,
            period_end=result.period_end,
            rows_appended=result.rows_appended,
            historical_observations=status.historical_observations,
            real_observations=status.real_observations,
            simulated_observations=status.simulated_observations,
            segment_count=status.segment_count,
        )
        db.commit()
        return payload
    except MerchantOnboardingNotFoundError as exc:
        db.rollback()
        raise _error(404, "DEMO_NOT_AVAILABLE", _safe_message(exc)) from None
    except IncrementalDataConflictError as exc:
        db.rollback()
        raise _error(409, "DEMO_PERIOD_CONFLICT", _safe_message(exc)) from None
    except IncrementalDataSourceError as exc:
        db.rollback()
        raise _error(409, "DEMO_DATA_UNAVAILABLE", _safe_message(exc)) from None
    except IncrementalDataError as exc:
        db.rollback()
        raise _error(422, "DATA_UPDATE_INVALID", _safe_message(exc)) from None
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("unexpected demo-period append failure")
        raise _error(500, "DATA_UPDATE_FAILED", "Demo period could not be appended") from None


__all__ = ["router"]
