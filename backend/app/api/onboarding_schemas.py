"""Pydantic contracts for Task 21A merchant onboarding."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class OnboardedMerchantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    merchant_id: str
    name: str
    category: str | None
    monthly_gmv_paise: int | None
    created_at: datetime | None
    data_source: str
    rows_imported: int
    historical_observations: int
    real_observations: int
    simulated_observations: int
    segment_count: int


class MerchantDataStatusResponse(BaseModel):
    merchant_id: str
    data_source: str
    historical_observations: int
    real_observations: int
    simulated_observations: int
    segment_count: int
    has_data: bool


class DemoMerchantResponse(BaseModel):
    merchant_id: str
    name: str
    data_source: str
    historical_observations: int
    segment_count: int
