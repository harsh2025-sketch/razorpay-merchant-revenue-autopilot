"""Focused tests for the explicit hosted-demo Razorpay simulation boundary."""

from __future__ import annotations

from app.config import Settings
from app.services.razorpay import RazorpayClient


def test_simulated_settings_need_no_real_credentials(monkeypatch):
    monkeypatch.setenv("RAZORPAY_EXECUTION_MODE", "simulated")
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)

    settings = Settings(_env_file=None)

    assert settings.RAZORPAY_EXECUTION_MODE == "simulated"
    assert settings.RAZORPAY_KEY_ID == "demo_simulated_key"
    assert settings.RAZORPAY_KEY_SECRET == "demo_simulated_secret"


def test_real_settings_still_fail_closed_without_credentials(monkeypatch):
    monkeypatch.setenv("RAZORPAY_EXECUTION_MODE", "real")
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)

    settings = Settings(_env_file=None)

    assert settings.RAZORPAY_EXECUTION_MODE == "real"
    assert settings.RAZORPAY_KEY_ID is None
    assert settings.RAZORPAY_KEY_SECRET is None


def test_simulated_payment_link_never_builds_http_client(monkeypatch):
    monkeypatch.setenv("RAZORPAY_EXECUTION_MODE", "simulated")

    def fail_if_network_client_is_built(self):  # pragma: no cover - failure path
        raise AssertionError("simulated mode must not construct an HTTP client")

    monkeypatch.setattr(RazorpayClient, "_build_http_client", fail_if_network_client_is_built)

    client = RazorpayClient(
        key_id="demo_simulated_key",
        key_secret="demo_simulated_secret",
    )
    first = client.create_payment_link(
        amount=10000,
        currency="INR",
        reference_id="mra_experiment_123_treatment_v1",
        description="Merchant Revenue Autopilot test treatment",
        payment_methods={"card": True, "upi": False},
    )
    second = client.create_payment_link(
        amount=10000,
        currency="INR",
        reference_id="mra_experiment_123_treatment_v1",
        description="Merchant Revenue Autopilot test treatment",
        payment_methods={"card": True, "upi": False},
    )

    assert first == second
    assert first["id"].startswith("demo_plink_")
    assert first["execution_mode"] == "simulated"

    cancelled = client.cancel_payment_link(first["id"])
    assert cancelled == {
        "id": first["id"],
        "status": "cancelled",
        "execution_mode": "simulated",
    }
