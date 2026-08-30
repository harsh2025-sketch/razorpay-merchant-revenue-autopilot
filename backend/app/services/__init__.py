"""Application services layer.

Execution (Task 13), idempotency (Task 13) and the tamper-evident audit trail
(Task 14) are re-exported here.

The Task 15 lifecycle orchestration is deliberately **not** re-exported.
``app.services.autopilot`` depends on ``app.services.experiments``, which in
turn depends on ``app.simulation.runner`` - and that runner imports
``app.services.audit``. Importing the orchestration module from this package
initializer would therefore make ``import app.simulation`` (for example from
``scripts/seed_demo.py``) fail on a partially initialised module. Reach the
orchestration layer as ``from app.services import autopilot`` instead; its
generic action verbs (``plan``/``deploy``/``evaluate``/``rollback``) are meant
to be read as ``autopilot.plan(...)`` inside the lifecycle, not as service-wide
names.
"""

from app.services.audit import (
    AI_DIAGNOSIS_CREATED,
    ACTOR_AI,
    ACTOR_DETECTOR,
    ACTOR_PLANNER,
    ACTOR_POLICY,
    ACTOR_RAZORPAY_EXECUTOR,
    ACTOR_RUNTIME,
    ACTOR_STATISTICS,
    ACTOR_SYSTEM,
    AuditError,
    EXPERIMENT_COMPLETED,
    EXPERIMENT_PLANNED,
    EXPERIMENT_ROLLED_BACK,
    EXPERIMENT_STARTED,
    HYPOTHESIS_PROPOSED,
    OPPORTUNITY_DETECTED,
    POLICY_APPROVED,
    POLICY_REJECTED,
    RAZORPAY_RESOURCE_CANCELLED,
    RAZORPAY_RESOURCE_CREATED,
    TREATMENT_PROMOTED,
    get_experiment_audit_history,
    get_merchant_audit_history,
    has_audit_event,
    record_audit_event,
    record_audit_event_once,
    verify_merchant_audit_chain,
)
from app.services.executor import (
    DEPLOY_OPERATION_TYPE,
    DESCRIPTION,
    ExperimentExecutionAuthorizationError,
    ExperimentExecutionConfigurationError,
    ExperimentExecutionError,
    ExperimentExecutionStateError,
    ROLLBACK_OPERATION_TYPE,
    TEST_AMOUNT_PAISE,
    compute_expire_by,
    deploy_experiment_treatment,
    rollback_experiment_treatment,
)
from app.services.idempotency import (
    IdempotencyConflictError,
    IdempotencyError,
    IdempotencyInProgressError,
    begin_operation,
    compute_request_hash,
    mark_operation_ambiguous,
    mark_operation_failed,
    mark_operation_succeeded,
)

__all__ = [
    "AI_DIAGNOSIS_CREATED",
    "ACTOR_AI",
    "ACTOR_DETECTOR",
    "ACTOR_PLANNER",
    "ACTOR_POLICY",
    "ACTOR_RAZORPAY_EXECUTOR",
    "ACTOR_RUNTIME",
    "ACTOR_STATISTICS",
    "ACTOR_SYSTEM",
    "AuditError",
    "DEPLOY_OPERATION_TYPE",
    "DESCRIPTION",
    "ExperimentExecutionAuthorizationError",
    "ExperimentExecutionConfigurationError",
    "ExperimentExecutionError",
    "ExperimentExecutionStateError",
    "IdempotencyConflictError",
    "IdempotencyError",
    "IdempotencyInProgressError",
    "ROLLBACK_OPERATION_TYPE",
    "TEST_AMOUNT_PAISE",
    "begin_operation",
    "EXPERIMENT_COMPLETED",
    "EXPERIMENT_PLANNED",
    "EXPERIMENT_ROLLED_BACK",
    "EXPERIMENT_STARTED",
    "HYPOTHESIS_PROPOSED",
    "OPPORTUNITY_DETECTED",
    "POLICY_APPROVED",
    "POLICY_REJECTED",
    "RAZORPAY_RESOURCE_CANCELLED",
    "RAZORPAY_RESOURCE_CREATED",
    "TREATMENT_PROMOTED",
    "compute_expire_by",
    "compute_request_hash",
    "deploy_experiment_treatment",
    "get_experiment_audit_history",
    "get_merchant_audit_history",
    "has_audit_event",
    "mark_operation_ambiguous",
    "mark_operation_failed",
    "mark_operation_succeeded",
    "record_audit_event",
    "record_audit_event_once",
    "rollback_experiment_treatment",
    "verify_merchant_audit_chain",
]
