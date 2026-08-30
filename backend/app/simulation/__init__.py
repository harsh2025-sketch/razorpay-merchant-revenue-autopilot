"""Simulation package for Merchant Revenue Autopilot."""

from app.simulation.causal_model import (
    InterventionSpec,
    PaymentContext,
    SimulatedOutcome,
    causal_model_fingerprint,
    simulate_outcome,
)
from app.simulation.generator import BaselinePaymentEvent, generate_baseline_events
from app.simulation.merchant import TECHBAZAAR_PROFILE, MerchantProfile, SegmentProfile
from app.simulation.runner import (
    ExperimentRunSummary,
    ExperimentRuntimeError,
    assign_variant,
    run_experiment_batch,
)

__all__ = [
    "TECHBAZAAR_PROFILE",
    "MerchantProfile",
    "SegmentProfile",
    "BaselinePaymentEvent",
    "generate_baseline_events",
    "PaymentContext",
    "InterventionSpec",
    "SimulatedOutcome",
    "simulate_outcome",
    "causal_model_fingerprint",
    "ExperimentRunSummary",
    "ExperimentRuntimeError",
    "assign_variant",
    "run_experiment_batch",
]
