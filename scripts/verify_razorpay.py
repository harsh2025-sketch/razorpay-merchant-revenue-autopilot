#!/usr/bin/env python
"""MANUAL live verification of the Razorpay client boundary (Test Mode only).

This script is NOT part of pytest. It performs a small, fixed set of real
Razorpay Test Mode calls to prove the app can create / fetch / cancel the
exact resources the Revenue Autopilot needs. It is intentionally minimal:
no loops over synthetic customers, and every Payment Link it creates is
cancelled at the end.

Requirements:
    - RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET (TEST MODE keys, rzp_test_...)
    - optional: RAZORPAY_TEST_OFFER_ID (an Offer created in the Razorpay
      Dashboard; offers cannot be created via the API)

Run from the repository root:

    python scripts/verify_razorpay.py
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

# Make the backend package importable regardless of the caller's cwd.
BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.services.razorpay import RazorpayClient, RazorpayError  # noqa: E402

AMOUNT_PAISE = 100  # Rs 1.00 - small, deliberate test amount
PARTIAL_MIN_PAISE = 50  # first partial payment >= Rs 0.50


def main() -> int:
    settings = get_settings()
    key_id = settings.RAZORPAY_KEY_ID
    key_secret = settings.RAZORPAY_KEY_SECRET

    if not key_id or not key_secret:
        print(
            "RAZORPAY CHECK: NOT RUN - RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET "
            "are missing."
        )
        print(
            "Add Razorpay TEST MODE credentials to the environment "
            "(or backend/.env) and re-run."
        )
        return 1

    if not key_id.startswith("rzp_test_"):
        print(
            "RAZORPAY CHECK: ABORTED - RAZORPAY_KEY_ID does not look like a "
            "TEST MODE key (expected prefix 'rzp_test_'). "
            "Refusing to run against live mode."
        )
        return 1

    print(f"Credentials: TEST MODE key {key_id} (secret hidden)")
    print()

    results: list[tuple[str, bool]] = []
    created_link_ids: list[str] = []

    def record(name: str, ok: bool, detail: str) -> None:
        results.append((name, ok))
        print(f"{name}: {'PASS' if ok else 'FAIL'}{f' - {detail}' if detail else ''}")

    with RazorpayClient(key_id, key_secret) as client:
        try:
            # 1+2. Standard Payment Link, notifications disabled, then fetch.
            ref = f"autopilot-verify-std-{uuid.uuid4().hex[:12]}"
            link = client.create_payment_link(
                amount=AMOUNT_PAISE,
                currency="INR",
                reference_id=ref,
                description="Revenue Autopilot feasibility (standard link)",
                notify={"sms": False, "email": False},
            )
            created_link_ids.append(str(link.get("id", "")))
            fetched = client.fetch_payment_link(str(link["id"]))
            ok = fetched.get("id") == link.get("id")
            record(
                "STANDARD LINK (create+fetch)",
                ok,
                f"{link.get('id')} reference_id={ref}",
            )

            # 3+4. Customized Payment Link with a documented method config.
            ref = f"autopilot-verify-methods-{uuid.uuid4().hex[:12]}"
            custom = client.create_payment_link(
                amount=AMOUNT_PAISE,
                currency="INR",
                reference_id=ref,
                description="Revenue Autopilot feasibility (card+netbanking only)",
                notify={"sms": False, "email": False},
                payment_methods={
                    "card": True,
                    "netbanking": True,
                    "upi": False,
                    "wallet": False,
                },
            )
            created_link_ids.append(str(custom.get("id", "")))
            fetched = client.fetch_payment_link(str(custom["id"]))
            record(
                "CUSTOMIZED LINK (methods)",
                fetched.get("id") == custom.get("id"),
                str(custom.get("id")),
            )

            # 5. Partial-payment Payment Link.
            ref = f"autopilot-verify-partial-{uuid.uuid4().hex[:12]}"
            partial = client.create_payment_link(
                amount=AMOUNT_PAISE,
                currency="INR",
                reference_id=ref,
                description="Revenue Autopilot feasibility (partial payments)",
                notify={"sms": False, "email": False},
                accept_partial=True,
                first_min_partial_amount=PARTIAL_MIN_PAISE,
            )
            created_link_ids.append(str(partial.get("id", "")))
            record("PARTIAL LINK", True, str(partial.get("id")))

            # 6. Expiring Payment Link (1 hour ahead; Razorpay caps expiry at
            # six months from creation).
            ref = f"autopilot-verify-expiring-{uuid.uuid4().hex[:12]}"
            expiring = client.create_payment_link(
                amount=AMOUNT_PAISE,
                currency="INR",
                reference_id=ref,
                description="Revenue Autopilot feasibility (expiring link)",
                notify={"sms": False, "email": False},
                expire_by=int(time.time()) + 3600,
            )
            created_link_ids.append(str(expiring.get("id", "")))
            record("EXPIRING LINK", True, str(expiring.get("id")))

            # 7. Plain test Order.
            order = client.create_order(
                amount=AMOUNT_PAISE,
                currency="INR",
                receipt=f"verify-{uuid.uuid4().hex[:12]}",
            )
            record("PLAIN ORDER", True, str(order.get("id")))

            # 8. Optional offer check - Offers must already exist in the
            # Razorpay Dashboard; we only attach them to an Order.
            offer_id = settings.RAZORPAY_TEST_OFFER_ID
            if not offer_id:
                print(
                    "OFFER CHECK: SKIPPED - no pre-created offer configured "
                    "(set RAZORPAY_TEST_OFFER_ID to enable)"
                )
            else:
                try:
                    offer_order = client.create_order(
                        amount=AMOUNT_PAISE,
                        currency="INR",
                        receipt=f"verify-offer-{uuid.uuid4().hex[:12]}",
                        offer_ids=[offer_id],
                    )
                    record(
                        "OFFER CHECK", True,
                        f"order {offer_order.get('id')} with offer {offer_id}",
                    )
                except RazorpayError as exc:
                    record("OFFER CHECK", False, str(exc))

        except (RazorpayError, ValueError) as exc:
            record("FEASIBILITY STEPS", False, str(exc))

        finally:
            # Cancel any still-active Payment Links created by this script.
            for plink_id in created_link_ids:
                if not plink_id:
                    continue
                try:
                    current = client.fetch_payment_link(plink_id)
                    if current.get("status") == "created":
                        client.cancel_payment_link(plink_id)
                        print(f"CLEANUP: cancelled {plink_id}")
                    else:
                        print(
                            f"CLEANUP: {plink_id} status="
                            f"{current.get('status')!r}, not cancelling"
                        )
                except RazorpayError as exc:
                    print(f"CLEANUP: WARNING could not cancel {plink_id}: {exc}")

    failed = [name for name, ok in results if not ok]
    print()
    if failed:
        print(f"OVERALL: FAIL - failed steps: {', '.join(failed)}")
        return 1
    print(f"OVERALL: PASS ({len(results)} feasibility checks passed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
