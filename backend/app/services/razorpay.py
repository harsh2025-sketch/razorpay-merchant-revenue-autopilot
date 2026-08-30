"""Thin Razorpay HTTP client boundary.

This module owns all direct Razorpay HTTP traffic for the Revenue
Autopilot. It is intentionally a *thin, explicit* boundary:

- plain ``httpx`` with HTTP Basic Auth - no Razorpay SDK,
- fully visible request payloads, built right here,
- all argument validation happens BEFORE any network call,
- NO automatic retries: a timeout after a POST is ambiguous (the resource
  may still have been created on Razorpay's side), so retrying could
  create duplicates. Application-level idempotency via OperationExecution
  protects write operations,
- Razorpay Offers are never created from here. Offers are created in the
  Razorpay Dashboard; this client may only attach *existing* offer IDs to
  Orders,
- an explicit ``RAZORPAY_EXECUTION_MODE=simulated`` hosted-demo mode returns
  deterministic ``demo_plink_...`` resources and never performs HTTP traffic.
  These resources are deliberately distinguishable from real Razorpay IDs.

Security notes:

- credentials are passed in explicitly and never logged,
- simulated mode uses no merchant credentials and never reaches Razorpay,
- the key secret is redacted from exception messages.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any
from urllib.parse import quote

import httpx

__all__ = [
    "DEFAULT_BASE_URL",
    "PAYMENT_LINK_METHOD_KEYS",
    "RazorpayAuthenticationError",
    "RazorpayBadRequestError",
    "RazorpayClient",
    "RazorpayError",
    "RazorpayNotFoundError",
    "RazorpayServerError",
]

DEFAULT_BASE_URL = "https://api.razorpay.com/v1"

#: Payment-method keys accepted by the Payment Link checkout config.
PAYMENT_LINK_METHOD_KEYS = frozenset({"card", "netbanking", "upi", "wallet"})


# ---------------------------------------------------------------------------
# Error model
# ---------------------------------------------------------------------------


class RazorpayError(Exception):
    """Base class for all Razorpay client errors."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class RazorpayAuthenticationError(RazorpayError):
    """HTTP 401/403 - invalid credentials or unauthorized key."""


class RazorpayBadRequestError(RazorpayError):
    """HTTP 400 - Razorpay rejected the request as invalid."""


class RazorpayNotFoundError(RazorpayError):
    """HTTP 404 - the requested resource does not exist."""


class RazorpayServerError(RazorpayError):
    """HTTP 5xx - Razorpay server-side failure."""


# ---------------------------------------------------------------------------
# Validation helpers (all run before any network call)
# ---------------------------------------------------------------------------


def _require_non_empty_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _validate_amount(amount: object, label: str = "amount") -> None:
    # bool is an int subclass - reject it explicitly.
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise ValueError(f"{label} must be a positive integer (in currency subunits)")


def _validate_payment_methods(payment_methods: dict[str, bool]) -> dict[str, bool]:
    for key, value in payment_methods.items():
        if key not in PAYMENT_LINK_METHOD_KEYS:
            raise ValueError(
                f"invalid payment method {key!r}; allowed keys: "
                f"{sorted(PAYMENT_LINK_METHOD_KEYS)}"
            )
        if not isinstance(value, bool):
            raise ValueError(
                f"payment method {key!r} must be a boolean, "
                f"got {type(value).__name__}"
            )
    return dict(payment_methods)


def _parse_json_object(response: httpx.Response) -> dict:
    if not response.content:
        return {}
    try:
        data = response.json()
    except ValueError:
        raise RazorpayError(
            f"Razorpay returned a non-JSON response (HTTP {response.status_code})",
            status_code=response.status_code,
        ) from None
    if not isinstance(data, dict):
        raise RazorpayError(
            f"Razorpay returned an unexpected response shape "
            f"(HTTP {response.status_code})",
            status_code=response.status_code,
        )
    return data


def _redact(text: str, *secrets: str) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def _simulated_payment_link_id(reference_id: str) -> str:
    digest = hashlib.sha256(reference_id.encode("utf-8")).hexdigest()[:16]
    return f"demo_plink_{digest}"


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class RazorpayClient:
    """Thin HTTP boundary over the Razorpay API subset used by the app.

    The client performs exactly one HTTP request per method call and never
    retries automatically. When ``RAZORPAY_EXECUTION_MODE=simulated`` it
    performs zero HTTP requests and returns deterministic demo resources.
    """

    def __init__(
        self,
        key_id: str,
        key_secret: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not key_id or not key_id.strip():
            raise ValueError("key_id must be a non-empty string")
        if not key_secret or not key_secret.strip():
            raise ValueError("key_secret must be a non-empty string")

        self._key_id = key_id
        self._key_secret = key_secret
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._simulated = (
            os.environ.get("RAZORPAY_EXECUTION_MODE", "real").strip().lower()
            == "simulated"
        )
        self._client = None if self._simulated else self._build_http_client()

    def _build_http_client(self) -> httpx.Client:
        """Build the underlying ``httpx.Client``.

        Split out as a hook so tests can substitute an ``httpx.MockTransport``
        without changing the public constructor signature.
        """
        return httpx.Client(
            base_url=self._base_url,
            auth=(self._key_id, self._key_secret),  # HTTP Basic Auth
            timeout=self._timeout_seconds,
            headers={"Accept": "application/json"},
        )

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def __enter__(self) -> "RazorpayClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- Payment Links -------------------------------------------------------

    def create_payment_link(
        self,
        *,
        amount: int,
        currency: str = "INR",
        reference_id: str,
        description: str,
        customer: dict | None = None,
        notify: dict | None = None,
        accept_partial: bool = False,
        first_min_partial_amount: int | None = None,
        expire_by: int | None = None,
        payment_methods: dict[str, bool] | None = None,
        notes: dict | None = None,
    ) -> dict:
        """POST /payment_links - create a Payment Link or explicit demo resource."""
        _validate_amount(amount)
        reference_id = _require_non_empty_id(reference_id, "reference_id")

        if first_min_partial_amount is not None:
            if not accept_partial:
                raise ValueError(
                    "first_min_partial_amount can only be passed when "
                    "accept_partial=True"
                )
            _validate_amount(first_min_partial_amount, "first_min_partial_amount")
            if first_min_partial_amount > amount:
                raise ValueError(
                    "first_min_partial_amount must not exceed amount"
                )

        if expire_by is not None:
            if (
                not isinstance(expire_by, int)
                or isinstance(expire_by, bool)
                or expire_by <= 0
            ):
                raise ValueError("expire_by must be a positive Unix timestamp")

        methods = (
            _validate_payment_methods(payment_methods)
            if payment_methods is not None
            else None
        )

        payload: dict[str, Any] = {
            "amount": amount,
            "currency": currency,
            "reference_id": reference_id,
            "description": description,
            "accept_partial": accept_partial,
        }
        if customer is not None:
            payload["customer"] = customer
        if notify is not None:
            payload["notify"] = notify
        if first_min_partial_amount is not None:
            payload["first_min_partial_amount"] = first_min_partial_amount
        if expire_by is not None:
            payload["expire_by"] = expire_by
        if methods is not None:
            payload["options"] = {"checkout": {"method": methods}}
        if notes is not None:
            payload["notes"] = notes

        if self._simulated:
            return {
                "id": _simulated_payment_link_id(reference_id),
                "status": "created",
                "reference_id": reference_id,
                "execution_mode": "simulated",
            }

        return self._request("POST", "/payment_links", json=payload)

    def fetch_payment_link(self, payment_link_id: str) -> dict:
        """GET /payment_links/{id}."""
        plink_id = _require_non_empty_id(payment_link_id, "payment_link_id")
        if self._simulated:
            return {
                "id": plink_id,
                "status": "created",
                "execution_mode": "simulated",
            }
        return self._request("GET", f"/payment_links/{quote(plink_id, safe='')}")

    def cancel_payment_link(self, payment_link_id: str) -> dict:
        """POST /payment_links/{id}/cancel (no request body)."""
        plink_id = _require_non_empty_id(payment_link_id, "payment_link_id")
        if self._simulated:
            return {
                "id": plink_id,
                "status": "cancelled",
                "execution_mode": "simulated",
            }
        return self._request(
            "POST", f"/payment_links/{quote(plink_id, safe='')}/cancel"
        )

    # -- Orders ---------------------------------------------------------------

    def create_order(
        self,
        *,
        amount: int,
        currency: str = "INR",
        receipt: str | None = None,
        offer_ids: list[str] | None = None,
        notes: dict | None = None,
    ) -> dict:
        """POST /orders - create an Order.

        ``offer_ids`` may only reference Offers that already exist in the
        Razorpay Dashboard (they are created there, never via the API here).
        """
        _validate_amount(amount)

        payload: dict[str, Any] = {"amount": amount, "currency": currency}

        if offer_ids:
            cleaned: list[str] = []
            for offer_id in offer_ids:
                cleaned.append(_require_non_empty_id(offer_id, "offer id"))
            if len(set(cleaned)) != len(cleaned):
                raise ValueError("offer_ids must not contain duplicates")
            payload["offers"] = cleaned

        if receipt is not None:
            payload["receipt"] = receipt
        if notes is not None:
            payload["notes"] = notes

        if self._simulated:
            receipt_seed = receipt or f"{amount}:{currency}"
            digest = hashlib.sha256(receipt_seed.encode("utf-8")).hexdigest()[:16]
            return {
                "id": f"demo_order_{digest}",
                "status": "created",
                "execution_mode": "simulated",
            }

        return self._request("POST", "/orders", json=payload)

    # -- Payments -------------------------------------------------------------

    def fetch_payment(self, payment_id: str) -> dict:
        """GET /payments/{id}."""
        pay_id = _require_non_empty_id(payment_id, "payment_id")
        if self._simulated:
            return {
                "id": pay_id,
                "status": "captured",
                "execution_mode": "simulated",
            }
        return self._request("GET", f"/payments/{quote(pay_id, safe='')}")

    # -- HTTP plumbing ----------------------------------------------------------

    def _request(self, method: str, path: str, json: dict | None = None) -> dict:
        """Perform exactly one HTTP request. Never retries."""
        if self._simulated or self._client is None:
            raise RazorpayError(
                "simulated Razorpay mode attempted an unsupported raw HTTP operation"
            )
        try:
            response = self._client.request(method, path, json=json)
        except httpx.RequestError as exc:
            raise RazorpayError(
                "Razorpay request failed before a response was received: "
                f"{exc.__class__.__name__}: {_redact(str(exc), self._key_secret)}"
            ) from exc

        if 200 <= response.status_code < 300:
            return _parse_json_object(response)

        raise self._error_for_response(response)

    def _error_for_response(self, response: httpx.Response) -> RazorpayError:
        status = response.status_code
        message = self._safe_message(response)
        if status in (401, 403):
            error_cls: type[RazorpayError] = RazorpayAuthenticationError
        elif status == 400:
            error_cls = RazorpayBadRequestError
        elif status == 404:
            error_cls = RazorpayNotFoundError
        elif 500 <= status <= 599:
            error_cls = RazorpayServerError
        else:
            error_cls = RazorpayError
        return error_cls(message, status_code=status)

    def _safe_message(self, response: httpx.Response) -> str:
        """Build a short, secret-free error message from a failed response."""
        description = ""
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                description = str(error.get("description") or error.get("message") or "")
            if not description:
                description = str(body.get("message") or "")
        if not description:
            description = (response.text or "").strip()
        description = _redact(description[:300], self._key_secret)
        if not description:
            description = f"HTTP {response.status_code}"
        return f"Razorpay API error (HTTP {response.status_code}): {description}"
