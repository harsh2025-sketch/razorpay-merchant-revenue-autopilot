"""Closed response shapes for Task 21B incremental data updates."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IncrementalCsvResponse(DataModel):
    merchant_id: str
    data_source: str
    rows_received: int
    rows_appended: int
    rows_deduplicated: int
    historical_observations: int
    real_observations: int
    simulated_observations: int
    segment_count: int


class DemoPeriodResponse(DataModel):
    merchant_id: str
    data_source: str
    period_index: int
    period_start: datetime
    period_end: datetime
    rows_appended: int
    historical_observations: int
    real_observations: int
    simulated_observations: int
    segment_count: int
