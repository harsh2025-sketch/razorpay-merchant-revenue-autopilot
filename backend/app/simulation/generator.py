"""Deterministic baseline payment event generator for demo merchant data."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import random
from typing import List, Optional

from app.simulation.merchant import TECHBAZAAR_PROFILE, MerchantProfile, SegmentProfile


@dataclass(frozen=True)
class BaselinePaymentEvent:
    id: str
    merchant_id: str
    customer_ref: Optional[str]
    amount: int
    currency: str
    payment_method: Optional[str]
    status: str
    failure_reason: Optional[str]
    device_type: Optional[str]
    segment: Optional[str]
    source: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]
    is_simulated: bool = True


def generate_baseline_events(
    *,
    profile: MerchantProfile = TECHBAZAAR_PROFILE,
    seed: int = 20260827,
    days: int = 30,
) -> List[BaselinePaymentEvent]:
    """Generate a reproducible 30-day baseline historical payment dataset.

    Uses an isolated RNG seeded with `seed`. Events are sorted chronologically
    and assigned deterministic IDs (pa_baseline_000001 ...).
    """
    rng = random.Random(seed)

    # Parse anchor timestamp and compute start date for the `days` window
    anchor_dt = datetime.fromisoformat(profile.anchor_timestamp_iso)
    if anchor_dt.tzinfo is None:
        anchor_dt = anchor_dt.replace(tzinfo=timezone.utc)

    # 30 calendar days ending on anchor_dt.date()
    start_date = anchor_dt.date() - timedelta(days=days - 1)

    # Pre-generate a pool of synthetic customer refs for repeat_buyer segment
    repeat_customer_pool = [f"cust_{rng.randint(1, 999):06d}" for _ in range(60)]
    unique_customer_seq = 1000

    raw_events = []

    # Segment sampling weights
    segments = list(profile.segments)
    segment_weights = [s.traffic_weight for s in segments]

    for day_idx in range(days):
        day_date = start_date + timedelta(days=day_idx)

        # Daily attempts volume with small deterministic variance (~200 per day)
        daily_attempts = rng.randint(
            profile.daily_attempts_target - 15,
            profile.daily_attempts_target + 15,
        )

        for _ in range(daily_attempts):
            # Select segment
            seg: SegmentProfile = rng.choices(segments, weights=segment_weights, k=1)[0]

            # Select customer_ref
            if seg.name == "repeat_buyer":
                customer_ref = rng.choice(repeat_customer_pool)
            else:
                # 95% unique customer, 5% repeat customer from pool
                if rng.random() < 0.05:
                    customer_ref = rng.choice(repeat_customer_pool)
                else:
                    customer_ref = f"cust_{unique_customer_seq:06d}"
                    unique_customer_seq += 1

            # Select order amount in paise (step of 100 paise = ₹1)
            min_step = seg.min_amount_paise // 100
            max_step = seg.max_amount_paise // 100
            amount = rng.randint(min_step, max_step) * 100

            # Select payment method
            pm_methods = list(seg.payment_method_weights.keys())
            pm_weights = list(seg.payment_method_weights.values())
            payment_method = rng.choices(pm_methods, weights=pm_weights, k=1)[0]

            # Select device type
            device_type = rng.choices(seg.device_types, weights=seg.device_weights, k=1)[0]

            # Select source
            src_names = list(seg.source_weights.keys())
            src_weights = list(seg.source_weights.values())
            source = rng.choices(src_names, weights=src_weights, k=1)[0]

            # Determine conversion status & failure reason
            status_roll = rng.random()
            if status_roll < seg.target_conversion_rate:
                status = "captured"
                failure_reason = None
            else:
                # Non-captured: 60% failed, 40% abandoned
                if rng.random() < 0.60:
                    status = "failed"
                    fr_names = list(seg.failure_reason_weights.keys())
                    fr_weights = list(seg.failure_reason_weights.values())
                    failure_reason = rng.choices(fr_names, weights=fr_weights, k=1)[0]
                else:
                    status = "abandoned"
                    failure_reason = None

            # Generate realistic creation timestamp within the day
            # Diurnal weighting: more events during day/evening hours (8am - 10pm)
            hour_roll = rng.random()
            if hour_roll < 0.80:
                hour = rng.randint(8, 22)
            else:
                hour = rng.choice([0, 1, 2, 3, 4, 5, 6, 7, 23])

            minute = rng.randint(0, 59)
            second = rng.randint(0, 59)
            microsecond = rng.randint(0, 999999)

            created_at = datetime(
                day_date.year,
                day_date.month,
                day_date.day,
                hour,
                minute,
                second,
                microsecond,
                tzinfo=timezone.utc,
            )

            # Generate completed_at timestamp
            if status == "captured":
                duration_seconds = rng.randint(5, 120)
                completed_at = created_at + timedelta(seconds=duration_seconds)
            elif status == "failed":
                duration_seconds = rng.randint(2, 45)
                completed_at = created_at + timedelta(seconds=duration_seconds)
            else:
                # abandoned
                completed_at = None

            raw_events.append(
                {
                    "merchant_id": profile.merchant_id,
                    "customer_ref": customer_ref,
                    "amount": amount,
                    "currency": profile.currency,
                    "payment_method": payment_method,
                    "status": status,
                    "failure_reason": failure_reason,
                    "device_type": device_type,
                    "segment": seg.name,
                    "source": source,
                    "created_at": created_at,
                    "completed_at": completed_at,
                    "is_simulated": True,
                }
            )

    # Sort raw events chronologically by created_at
    raw_events.sort(key=lambda x: x["created_at"])

    # Create BaselinePaymentEvent dataclasses with deterministic IDs
    events = []
    for idx, event_data in enumerate(raw_events, start=1):
        event_id = f"pa_baseline_{idx:06d}"
        events.append(
            BaselinePaymentEvent(
                id=event_id,
                **event_data,
            )
        )

    return events
