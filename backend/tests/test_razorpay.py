"""Unit tests for the Razorpay client boundary (Task 03).

All HTTP traffic is mocked with ``httpx.MockTransport`` - no real Razorpay
calls are made. The mock transport is injected by overriding the private
``_build_http_client`` hook, leaving the public constructor unchanged.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.services.razorpay import (
    RazorpayAuthenticationError,
    RazorpayBadRequestError,
    RazorpayClient,
    RazorpayError,
    RazorpayNotFoundError,
    RazorpayServerError,
)

KEY_ID = "rzp_test_keyid123"
KEY_SECRET = "super_secret_do_not_leak_42"


def json_response(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body)


def make_client(
    handler,
    *,
    key_id: str = KEY_ID,
    key_secret: str = KEY_SECRET,
):
    """Build a RazorpayClient backed by a recording MockTransport.

    Returns ``(client, requests)`` where ``requests`` is the list of
    ``httpx.Request`` objects seen by the transport, in order.
    """
    seen: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    class MockedRazorpayClient(RazorpayClient):
        def _build_http_client(self) -> httpx.Client:
            return httpx.Client(
                base_url=self._base_url,
                auth=(self._key_id, self._key_secret),
                timeout=self._timeout_seconds,
                transport=httpx.MockTransport(recording_handler),
            )

    client = MockedRazorpayClient(key_id, key_secret)
    return client, seen


def ok_payment_link_body(amount: int = 50000, reference_id: str = "ref-1") -> dict:
    return {
        "id": "plink_test123",
        "amount": amount,
        "currency": "INR",
        "reference_id": reference_id,
        "description": "Test payment link",
        "status": "created",
        "accept_partial": False,
        "short_url": "https://rzp.io/i/plink_test123",
    }


def ok_order_body(amount: int = 50000) -> dict:
    return {
        "id": "order_test123",
        "amount": amount,
        "currency": "INR",
        "status": "created",
    }


def always_ok(body: dict):
    return lambda request: json_response(200, body)


# ---------------------------------------------------------------------------
# 1. Standard Payment Link creation
# ---------------------------------------------------------------------------


def test_create_payment_link_standard() -> None:
    client, seen = make_client(
        always_ok(ok_payment_link_body(reference_id="order-exp-1"))
    )

    result = client.create_payment_link(
        amount=50000,
        currency="INR",
        reference_id="order-exp-1",
        description="Renewal nudge offer",
    )

    assert result["id"] == "plink_test123"
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/payment_links"

    payload = json.loads(request.content)
    assert payload["amount"] == 50000
    assert payload["currency"] == "INR"
    assert payload["reference_id"] == "order-exp-1"


def test_client_uses_http_basic_auth() -> None:
    client, seen = make_client(always_ok(ok_order_body()))
    client.create_order(amount=100)

    expected = base64.b64encode(f"{KEY_ID}:{KEY_SECRET}".encode()).decode()
    assert seen[0].headers["Authorization"] == f"Basic {expected}"


# ---------------------------------------------------------------------------
# 2. Payment-method customization produces exactly options.checkout.method
# ---------------------------------------------------------------------------


def test_payment_method_config_shape() -> None:
    client, seen = make_client(always_ok(ok_payment_link_body()))

    client.create_payment_link(
        amount=50000,
        reference_id="ref-methods",
        description="method customization",
        payment_methods={
            "netbanking": True,
            "card": True,
            "upi": False,
            "wallet": False,
        },
    )

    payload = json.loads(seen[0].content)
    assert payload["options"] == {
        "checkout": {
            "method": {
                "netbanking": True,
                "card": True,
                "upi": False,
                "wallet": False,
            }
        }
    }
    # No other config fields are invented.
    assert set(payload["options"]) == {"checkout"}
    assert set(payload["options"]["checkout"]) == {"method"}


def test_no_options_block_when_payment_methods_omitted() -> None:
    client, seen = make_client(always_ok(ok_payment_link_body()))
    client.create_payment_link(amount=100, reference_id="r", description="d")
    assert "options" not in json.loads(seen[0].content)


# ---------------------------------------------------------------------------
# 3. Partial payment fields
# ---------------------------------------------------------------------------


def test_partial_payment_fields_passed() -> None:
    client, seen = make_client(always_ok(ok_payment_link_body()))

    client.create_payment_link(
        amount=70000,
        reference_id="ref-partial",
        description="partial payments allowed",
        accept_partial=True,
        first_min_partial_amount=20000,
    )

    payload = json.loads(seen[0].content)
    assert payload["accept_partial"] is True
    assert payload["first_min_partial_amount"] == 20000


def test_accept_partial_false_omits_first_min_amount() -> None:
    client, seen = make_client(always_ok(ok_payment_link_body()))
    client.create_payment_link(amount=70000, reference_id="r", description="d")
    payload = json.loads(seen[0].content)
    assert payload["accept_partial"] is False
    assert "first_min_partial_amount" not in payload


# ---------------------------------------------------------------------------
# 4. first_min_partial_amount rejected without accept_partial
# ---------------------------------------------------------------------------


def test_first_min_partial_amount_requires_accept_partial() -> None:
    client, seen = make_client(always_ok(ok_payment_link_body()))

    with pytest.raises(ValueError, match="accept_partial"):
        client.create_payment_link(
            amount=70000,
            reference_id="ref-bad-partial",
            description="should fail",
            first_min_partial_amount=100,
        )

    assert seen == []  # rejected before any network call


# ---------------------------------------------------------------------------
# 5. Expiry
# ---------------------------------------------------------------------------


def test_expire_by_included_when_provided() -> None:
    client, seen = make_client(always_ok(ok_payment_link_body()))

    client.create_payment_link(
        amount=50000,
        reference_id="ref-expiry",
        description="expiring link",
        expire_by=1893456000,
    )

    payload = json.loads(seen[0].content)
    assert payload["expire_by"] == 1893456000


def test_expire_by_omitted_when_not_provided() -> None:
    client, seen = make_client(always_ok(ok_payment_link_body()))
    client.create_payment_link(amount=100, reference_id="r", description="d")
    assert "expire_by" not in json.loads(seen[0].content)


def test_non_positive_expire_by_rejected() -> None:
    client, seen = make_client(always_ok(ok_payment_link_body()))
    with pytest.raises(ValueError, match="expire_by"):
        client.create_payment_link(
            amount=100,
            reference_id="r",
            description="d",
            expire_by=0,
        )
    assert seen == []


# ---------------------------------------------------------------------------
# 6-7. Fetch / cancel Payment Link endpoints
# ---------------------------------------------------------------------------


def test_fetch_payment_link_endpoint() -> None:
    client, seen = make_client(always_ok(ok_payment_link_body()))

    client.fetch_payment_link("plink_abc123")

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "GET"
    assert request.url.path == "/v1/payment_links/plink_abc123"


def test_cancel_payment_link_endpoint() -> None:
    client, seen = make_client(
        always_ok({"id": "plink_abc123", "status": "cancelled"})
    )

    result = client.cancel_payment_link("plink_abc123")

    assert result["status"] == "cancelled"
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/payment_links/plink_abc123/cancel"
    # No request body unless the endpoint requires one.
    assert request.content == b""


# ---------------------------------------------------------------------------
# 8-10. Orders
# ---------------------------------------------------------------------------


def test_create_order_without_offers() -> None:
    client, seen = make_client(always_ok(ok_order_body()))

    result = client.create_order(amount=25000, currency="INR", receipt="rcpt-1")

    assert result["id"] == "order_test123"
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/orders"

    payload = json.loads(request.content)
    assert payload == {"amount": 25000, "currency": "INR", "receipt": "rcpt-1"}
    assert "offers" not in payload


def test_create_order_with_existing_offer_id() -> None:
    client, seen = make_client(always_ok(ok_order_body()))

    client.create_order(amount=25000, offer_ids=["offer_test123"])

    payload = json.loads(seen[0].content)
    assert payload["offers"] == ["offer_test123"]


def test_duplicate_offer_ids_rejected_before_network_call() -> None:
    client, seen = make_client(always_ok(ok_order_body()))

    with pytest.raises(ValueError, match="duplicate"):
        client.create_order(amount=25000, offer_ids=["offer_a", "offer_a"])

    assert seen == []


def test_empty_offer_id_rejected_before_network_call() -> None:
    client, seen = make_client(always_ok(ok_order_body()))
    with pytest.raises(ValueError, match="offer id"):
        client.create_order(amount=25000, offer_ids=["  "])
    assert seen == []


# ---------------------------------------------------------------------------
# 11. Fetch Payment endpoint
# ---------------------------------------------------------------------------


def test_fetch_payment_endpoint() -> None:
    client, seen = make_client(
        always_ok({"id": "pay_abc123", "amount": 25000, "status": "captured"})
    )

    result = client.fetch_payment("pay_abc123")

    assert result["id"] == "pay_abc123"
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "GET"
    assert request.url.path == "/v1/payments/pay_abc123"


# ---------------------------------------------------------------------------
# 12-16. Error mapping
# ---------------------------------------------------------------------------


def razorpay_error_body(description: str) -> dict:
    return {"error": {"code": "BAD_REQUEST_ERROR", "description": description}}


@pytest.mark.parametrize(
    ("status", "expected_cls"),
    [
        (400, RazorpayBadRequestError),
        (401, RazorpayAuthenticationError),
        (403, RazorpayAuthenticationError),
        (404, RazorpayNotFoundError),
        (500, RazorpayServerError),
        (503, RazorpayServerError),
    ],
)
def test_error_status_mapping(status: int, expected_cls: type[RazorpayError]) -> None:
    client, _ = make_client(
        lambda request: json_response(status, razorpay_error_body("boom"))
    )

    with pytest.raises(expected_cls) as excinfo:
        client.fetch_payment("pay_x")

    assert excinfo.value.status_code == status
    assert isinstance(excinfo.value, RazorpayError)


def test_unexpected_status_maps_to_base_error() -> None:
    client, _ = make_client(
        lambda request: json_response(418, razorpay_error_body("teapot"))
    )
    with pytest.raises(RazorpayError) as excinfo:
        client.fetch_payment("pay_x")
    assert not isinstance(excinfo.value, (RazorpayBadRequestError, RazorpayAuthenticationError))
    assert excinfo.value.status_code == 418


def test_key_secret_never_appears_in_exception_string() -> None:
    client, _ = make_client(
        lambda request: httpx.Response(
            401,
            json=razorpay_error_body("auth failed"),
        )
    )

    with pytest.raises(RazorpayError) as excinfo:
        client.fetch_payment("pay_x")

    assert KEY_SECRET not in str(excinfo.value)
    assert KEY_SECRET not in str(excinfo.value.__cause__)
    assert excinfo.value.status_code == 401


def test_error_message_includes_api_description() -> None:
    client, _ = make_client(
        lambda request: json_response(
            400, razorpay_error_body("amount is required")
        )
    )
    with pytest.raises(RazorpayBadRequestError, match="amount is required"):
        client.fetch_payment("pay_x")


# ---------------------------------------------------------------------------
# 17. Payment-method validation
# ---------------------------------------------------------------------------


def test_invalid_payment_method_name_rejected_before_request() -> None:
    client, seen = make_client(always_ok(ok_payment_link_body()))

    with pytest.raises(ValueError, match="invalid payment method"):
        client.create_payment_link(
            amount=100,
            reference_id="r",
            description="d",
            payment_methods={"crypto": True},
        )

    assert seen == []


def test_non_boolean_payment_method_value_rejected_before_request() -> None:
    client, seen = make_client(always_ok(ok_payment_link_body()))

    with pytest.raises(ValueError, match="boolean"):
        client.create_payment_link(
            amount=100,
            reference_id="r",
            description="d",
            payment_methods={"upi": "yes"},
        )

    assert seen == []


# ---------------------------------------------------------------------------
# 18. Amount validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("amount", [0, -1, -50000])
def test_non_positive_amount_rejected_before_request(amount: int) -> None:
    client, seen = make_client(always_ok(ok_payment_link_body()))

    with pytest.raises(ValueError, match="positive"):
        client.create_payment_link(
            amount=amount, reference_id="r", description="d"
        )
    with pytest.raises(ValueError, match="positive"):
        client.create_order(amount=amount)

    assert seen == []


def test_first_min_partial_amount_above_amount_rejected() -> None:
    client, seen = make_client(always_ok(ok_payment_link_body()))
    with pytest.raises(ValueError, match="exceed"):
        client.create_payment_link(
            amount=100,
            reference_id="r",
            description="d",
            accept_partial=True,
            first_min_partial_amount=200,
        )
    assert seen == []


# ---------------------------------------------------------------------------
# 19. Empty resource IDs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.fetch_payment_link(""),
        lambda c: c.fetch_payment_link("   "),
        lambda c: c.cancel_payment_link(""),
        lambda c: c.fetch_payment(""),
        lambda c: c.fetch_payment_link(None),
    ],
    ids=["empty-plink-id", "blank-plink-id", "empty-cancel-id", "empty-payment-id", "none-plink-id"],
)
def test_empty_resource_ids_rejected_before_request(call) -> None:
    client, seen = make_client(always_ok(ok_payment_link_body()))

    with pytest.raises(ValueError):
        call(client)

    assert seen == []


def test_empty_key_credentials_rejected() -> None:
    with pytest.raises(ValueError):
        RazorpayClient("", KEY_SECRET)
    with pytest.raises(ValueError):
        RazorpayClient(KEY_ID, "")


# ---------------------------------------------------------------------------
# 20. No automatic retries
# ---------------------------------------------------------------------------


def test_no_retry_on_post_server_failure() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return json_response(500, razorpay_error_body("internal error"))

    client, _ = make_client(handler)

    with pytest.raises(RazorpayServerError):
        client.create_payment_link(
            amount=100, reference_id="r", description="d"
        )

    assert calls["count"] == 1


def test_no_retry_on_post_network_timeout() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.ConnectTimeout("connection timed out", request=request)

    client, _ = make_client(handler)

    with pytest.raises(RazorpayError) as excinfo:
        client.create_order(amount=100)

    assert calls["count"] == 1
    # Ambiguous network failure must surface as a plain RazorpayError, and
    # the key secret must not leak through the transport message either.
    assert not isinstance(
        excinfo.value,
        (RazorpayBadRequestError, RazorpayAuthenticationError, RazorpayNotFoundError),
    )
    assert KEY_SECRET not in str(excinfo.value)


def test_no_retry_on_get_failure() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return json_response(502, {"error": {"description": "bad gateway"}})

    client, _ = make_client(handler)
    with pytest.raises(RazorpayServerError):
        client.fetch_payment("pay_x")
    assert calls["count"] == 1
