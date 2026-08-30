"""Core domain models for the Merchant Revenue Autopilot.

All models use portable SQLAlchemy types compatible with both SQLite and
PostgreSQL.  Money amounts are stored in **paise** (integer).  Primary keys
are Python-generated UUID strings.  Timestamps are timezone-aware UTC.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ 1. Merchant


class Merchant(Base):
    __tablename__ = "merchants"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    monthly_gmv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    # Relationships (minimal navigation only)
    policies: Mapped[list["MerchantPolicy"]] = relationship(
        back_populates="merchant", lazy="select"
    )


# --------------------------------------------------------- 2. MerchantPolicy


class MerchantPolicy(Base):
    __tablename__ = "merchant_policies"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchants.id"), nullable=False, unique=True
    )

    max_experiment_exposure_pct: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.10
    )
    max_discount_pct: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.15
    )
    min_margin_pct: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.05
    )
    max_concurrent_experiments: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3
    )
    max_experiment_duration_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=168
    )
    min_sample_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30
    )
    max_financial_exposure: Mapped[int] = mapped_column(
        Integer, nullable=False, default=50000
    )

    allowed_interventions: Mapped[dict] = mapped_column(JSON, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    merchant: Mapped["Merchant"] = relationship(back_populates="policies")


# --------------------------------------------------------- 3. PaymentAttempt


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"
    __table_args__ = (
        Index("ix_payment_attempts_merchant_status_created", "merchant_id", "status", "created_at"),
        Index("ix_payment_attempts_experiment_variant_status", "experiment_id", "variant", "status"),
        Index("ix_payment_attempts_merchant_segment_created", "merchant_id", "segment", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchants.id"), nullable=False
    )

    customer_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    internal_order_ref: Mapped[str | None] = mapped_column(String, nullable=True)

    razorpay_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    razorpay_payment_id: Mapped[str | None] = mapped_column(String, nullable=True)
    razorpay_payment_link_id: Mapped[str | None] = mapped_column(String, nullable=True)

    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False, default="INR")

    payment_method: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    device_type: Mapped[str | None] = mapped_column(String, nullable=True)
    segment: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)

    experiment_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("experiments.id"), nullable=True
    )
    variant: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    is_simulated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )


# --------------------------------------------------------- 4. Opportunity


class Opportunity(Base):
    __tablename__ = "opportunities"
    __table_args__ = (
        Index("ix_opportunities_merchant_status_created", "merchant_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchants.id"), nullable=False
    )

    type: Mapped[str] = mapped_column(String, nullable=False)
    segment: Mapped[str | None] = mapped_column(String, nullable=True)
    severity: Mapped[float] = mapped_column(Float, nullable=False)

    detected_metric: Mapped[str] = mapped_column(String, nullable=False)
    detected_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(
        String, nullable=False, default="detected"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


# --------------------------------------------------------- 5. Hypothesis


class Hypothesis(Base):
    __tablename__ = "hypotheses"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    opportunity_id: Mapped[str] = mapped_column(
        String, ForeignKey("opportunities.id"), nullable=False
    )
    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchants.id"), nullable=False
    )

    ai_model: Mapped[str | None] = mapped_column(String, nullable=True)

    hypothesis_text: Mapped[str] = mapped_column(Text, nullable=False)
    intervention_type: Mapped[str] = mapped_column(String, nullable=False)
    intervention_params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    confidence: Mapped[str | None] = mapped_column(String, nullable=True)

    reasoning_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    evidence_refs: Mapped[dict] = mapped_column(JSON, nullable=False, default=list)

    status: Mapped[str] = mapped_column(
        String, nullable=False, default="proposed"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


# --------------------------------------------------------- 6. Experiment


class Experiment(Base):
    __tablename__ = "experiments"
    __table_args__ = (
        Index("ix_experiments_merchant_status", "merchant_id", "status"),
        Index("ix_experiments_segment_status", "segment", "status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchants.id"), nullable=False
    )
    hypothesis_id: Mapped[str] = mapped_column(
        String, ForeignKey("hypotheses.id"), nullable=False
    )
    opportunity_id: Mapped[str] = mapped_column(
        String, ForeignKey("opportunities.id"), nullable=False
    )

    name: Mapped[str] = mapped_column(String, nullable=False)
    segment: Mapped[str] = mapped_column(String, nullable=False)
    intervention_type: Mapped[str] = mapped_column(String, nullable=False)

    control_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    treatment_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    traffic_split_treatment_pct: Mapped[float] = mapped_column(Float, nullable=False)

    primary_metric: Mapped[str] = mapped_column(String, nullable=False)
    guardrail_metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=list)

    min_sample_per_variant: Mapped[int] = mapped_column(Integer, nullable=False)
    max_duration_hours: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(
        String, nullable=False, default="proposed"
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


# ------------------------------------------------- 7. ExperimentAssignment


class ExperimentAssignment(Base):
    __tablename__ = "experiment_assignments"
    __table_args__ = (
        UniqueConstraint("experiment_id", "customer_ref", name="uq_assignment_experiment_customer"),
        Index("ix_experiment_assignments_experiment_variant", "experiment_id", "variant"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    experiment_id: Mapped[str] = mapped_column(
        String, ForeignKey("experiments.id"), nullable=False
    )
    customer_ref: Mapped[str] = mapped_column(String, nullable=False)
    variant: Mapped[str] = mapped_column(String, nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


# ------------------------------------------------- 8. ExperimentResult


class ExperimentResult(Base):
    __tablename__ = "experiment_results"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    experiment_id: Mapped[str] = mapped_column(
        String, ForeignKey("experiments.id"), nullable=False, unique=True
    )

    control_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    treatment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    control_conversions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    treatment_conversions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    control_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    treatment_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    absolute_lift: Mapped[float | None] = mapped_column(Float, nullable=True)
    relative_lift: Mapped[float | None] = mapped_column(Float, nullable=True)

    p_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_interval_lower: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_interval_upper: Mapped[float | None] = mapped_column(Float, nullable=True)

    is_significant: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    decision: Mapped[str | None] = mapped_column(String, nullable=True)

    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ------------------------------------------------- 9. PolicyDecision


class PolicyDecision(Base):
    __tablename__ = "policy_decisions"
    __table_args__ = (
        Index("ix_policy_decisions_experiment_evaluated", "experiment_id", "evaluated_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    experiment_id: Mapped[str] = mapped_column(
        String, ForeignKey("experiments.id"), nullable=False
    )
    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchants.id"), nullable=False
    )

    decision: Mapped[str] = mapped_column(String, nullable=False)

    violations: Mapped[dict] = mapped_column(JSON, nullable=False, default=list)
    original_params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    final_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


# ------------------------------------------------ 10. RazorpayResource


class RazorpayResource(Base):
    __tablename__ = "razorpay_resources"
    __table_args__ = (
        UniqueConstraint("resource_type", "razorpay_id", name="uq_razorpay_resource_type_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    experiment_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("experiments.id"), nullable=True
    )

    variant: Mapped[str | None] = mapped_column(String, nullable=True)

    resource_type: Mapped[str] = mapped_column(String, nullable=False)
    razorpay_id: Mapped[str] = mapped_column(String, nullable=False)

    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(
        String, nullable=False, default="active"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


# ------------------------------------------------- 11. AuditEvent


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_merchant_created", "merchant_id", "created_at"),
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchants.id"), nullable=False
    )

    event_type: Mapped[str] = mapped_column(String, nullable=False)

    entity_type: Mapped[str | None] = mapped_column(String, nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String, nullable=True)

    data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    actor: Mapped[str] = mapped_column(String, nullable=False)

    prev_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    event_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


# ---------------------------------------------- 12. OperationExecution


class OperationExecution(Base):
    __tablename__ = "operation_executions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)

    operation_key: Mapped[str] = mapped_column(
        String, nullable=False, unique=True
    )
    operation_type: Mapped[str] = mapped_column(String, nullable=False)

    request_payload_hash: Mapped[str] = mapped_column(String, nullable=False)

    status: Mapped[str] = mapped_column(String, nullable=False)

    razorpay_resource_id: Mapped[str | None] = mapped_column(String, nullable=True)
    response_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )
