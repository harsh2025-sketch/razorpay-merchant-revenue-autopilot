"""Tests for Task 13 application-level idempotency.

All tests are offline and use a temporary SQLite database. No commit is
performed by the service under test; callers control the transaction.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import OperationExecution
from app.services.idempotency import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    begin_operation,
    compute_request_hash,
    mark_operation_failed,
    mark_operation_succeeded,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
IDEMPOTENCY_PATH = BACKEND_DIR / "app" / "services" / "idempotency.py"


@pytest.fixture
def db_session(tmp_path):
    db_file = tmp_path / "test_exec_idem.db"
    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_new_operation_creates_pending_row(db_session):
    op = begin_operation(
        db_session,
        operation_key="op-1",
        operation_type="deploy_treatment",
        request_payload={"a": 1},
    )
    assert op.status == "pending"
    assert op.request_payload_hash == compute_request_hash({"a": 1})
    assert db_session.query(OperationExecution).count() == 1


def test_deterministic_request_hash():
    payload = {"a": 1, "b": {"x": True, "y": [1, 2, 3]}}
    assert compute_request_hash(payload) == compute_request_hash(payload)


def test_dictionary_key_order_does_not_change_hash():
    left = {"a": 1, "b": 2, "c": {"z": 3, "y": 4}}
    right = {"c": {"y": 4, "z": 3}, "b": 2, "a": 1}
    assert compute_request_hash(left) == compute_request_hash(right)


def test_succeeded_same_hash_reused(db_session):
    op1 = begin_operation(
        db_session,
        operation_key="op-same",
        operation_type="deploy_treatment",
        request_payload={"a": 1},
    )
    mark_operation_succeeded(
        db_session,
        op1,
        razorpay_resource_id="plink_1",
        response_json={"id": "plink_1", "status": "created"},
    )
    op2 = begin_operation(
        db_session,
        operation_key="op-same",
        operation_type="deploy_treatment",
        request_payload={"a": 1},
    )
    assert op2 is op1
    assert op2.status == "succeeded"
    assert op2.razorpay_resource_id == "plink_1"


def test_succeeded_different_hash_conflicts(db_session):
    begin_operation(
        db_session,
        operation_key="op-conf",
        operation_type="deploy_treatment",
        request_payload={"a": 1},
    )
    # Make it succeed directly; begin_operation will inspect state on next call.
    op = db_session.query(OperationExecution).one()
    mark_operation_succeeded(db_session, op, razorpay_resource_id="plink_2")
    with pytest.raises(IdempotencyConflictError):
        begin_operation(
            db_session,
            operation_key="op-conf",
            operation_type="deploy_treatment",
            request_payload={"a": 2},
        )


def test_pending_second_call_refused(db_session):
    begin_operation(
        db_session,
        operation_key="op-pending",
        operation_type="deploy_treatment",
        request_payload={"a": 1},
    )
    with pytest.raises(IdempotencyInProgressError):
        begin_operation(
            db_session,
            operation_key="op-pending",
            operation_type="deploy_treatment",
            request_payload={"a": 1},
        )


def test_failed_same_hash_transitions_to_pending(db_session):
    op = begin_operation(
        db_session,
        operation_key="op-retry",
        operation_type="deploy_treatment",
        request_payload={"a": 1},
    )
    mark_operation_failed(db_session, op, status_code=400)
    assert op.status == "failed"
    op2 = begin_operation(
        db_session,
        operation_key="op-retry",
        operation_type="deploy_treatment",
        request_payload={"a": 1},
    )
    assert op2 is op
    assert op2.status == "pending"
    assert op2.razorpay_resource_id is None


def test_failed_different_hash_conflicts(db_session):
    begin_operation(
        db_session,
        operation_key="op-failed-conf",
        operation_type="deploy_treatment",
        request_payload={"a": 1},
    )
    op = db_session.query(OperationExecution).one()
    mark_operation_failed(db_session, op, status_code=500)
    with pytest.raises(IdempotencyConflictError):
        begin_operation(
            db_session,
            operation_key="op-failed-conf",
            operation_type="deploy_treatment",
            request_payload={"a": 2},
        )


def test_idempotency_module_never_commits():
    tree = ast.parse(IDEMPOTENCY_PATH.read_text(encoding="utf-8"))
    source = IDEMPOTENCY_PATH.read_text(encoding="utf-8")
    assert ".commit(" not in source
    # Also no raw exception/secret values are persisted through this module.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "commit"


def test_secret_values_not_part_of_stored_payload(db_session):
    secret = "super_secret_do_not_leak_42"
    payload = {"key_id": "rzp_test_abc", "key_secret": secret}
    op = begin_operation(
        db_session,
        operation_key="op-secret",
        operation_type="deploy_treatment",
        request_payload=payload,
    )
    # The raw payload is not persisted; only a hash is.
    assert op.request_payload_hash != secret
    assert op.request_payload_hash != "rzp_test_abc"
    assert op.response_json is None

    mark_operation_failed(db_session, op, status_code=400, message=secret)
    serialized = json.dumps(op.response_json)
    assert secret not in serialized
    assert "rzp_test_abc" not in serialized
