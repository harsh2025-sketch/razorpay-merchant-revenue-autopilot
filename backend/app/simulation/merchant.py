"""Deterministic demo merchant profile definitions for TechBazaar Electronics."""

from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class SegmentProfile:
    name: str
    traffic_weight: float
    min_amount_paise: int
    max_amount_paise: int
    device_types: Tuple[str, ...]
    device_weights: Tuple[float, ...]
    target_conversion_rate: float
    payment_method_weights: Dict[str, float]
    source_weights: Dict[str, float]
    failure_reason_weights: Dict[str, float]


@dataclass(frozen=True)
class MerchantProfile:
    merchant_id: str
    name: str
    category: str
    currency: str
    monthly_gmv_paise: int
    daily_attempts_target: int
    days: int
    anchor_timestamp_iso: str
    segments: Tuple[SegmentProfile, ...]


TECHBAZAAR_PROFILE = MerchantProfile(
    merchant_id="merchant_techbazaar",
    name="TechBazaar Electronics",
    category="consumer_electronics",
    currency="INR",
    monthly_gmv_paise=500_000_000,  # ₹50,00,000 in paise
    daily_attempts_target=200,
    days=30,
    anchor_timestamp_iso="2026-08-26T23:59:59+00:00",
    segments=(
        SegmentProfile(
            name="android_mid",
            traffic_weight=0.25,
            min_amount_paise=100_000,  # ₹1,000
            max_amount_paise=350_000,  # ₹3,500
            device_types=("android",),
            device_weights=(1.0,),
            target_conversion_rate=0.53,
            payment_method_weights={
                "upi": 0.45,
                "card": 0.25,
                "netbanking": 0.20,
                "wallet": 0.10,
            },
            source_weights={
                "organic": 0.30,
                "paid_search": 0.30,
                "social": 0.20,
                "direct": 0.10,
                "email": 0.10,
            },
            failure_reason_weights={
                "bank_declined": 0.30,
                "authentication_failed": 0.25,
                "network_error": 0.20,
                "payment_timeout": 0.12,
                "insufficient_funds": 0.08,
                "unknown": 0.05,
            },
        ),
        SegmentProfile(
            name="android_budget",
            traffic_weight=0.35,
            min_amount_paise=50_000,  # ₹500
            max_amount_paise=150_000,  # ₹1,500
            device_types=("android",),
            device_weights=(1.0,),
            target_conversion_rate=0.48,
            payment_method_weights={
                "upi": 0.55,
                "card": 0.15,
                "netbanking": 0.15,
                "wallet": 0.15,
            },
            source_weights={
                "social": 0.35,
                "paid_search": 0.35,
                "organic": 0.15,
                "direct": 0.10,
                "email": 0.05,
            },
            failure_reason_weights={
                "insufficient_funds": 0.30,
                "bank_declined": 0.25,
                "authentication_failed": 0.20,
                "network_error": 0.15,
                "payment_timeout": 0.06,
                "unknown": 0.04,
            },
        ),
        SegmentProfile(
            name="web_general",
            traffic_weight=0.15,
            min_amount_paise=100_000,  # ₹1,000
            max_amount_paise=800_000,  # ₹8,000
            device_types=("web",),
            device_weights=(1.0,),
            target_conversion_rate=0.50,
            payment_method_weights={
                "upi": 0.25,
                "card": 0.40,
                "netbanking": 0.25,
                "wallet": 0.10,
            },
            source_weights={
                "organic": 0.35,
                "paid_search": 0.35,
                "direct": 0.15,
                "social": 0.10,
                "email": 0.05,
            },
            failure_reason_weights={
                "authentication_failed": 0.30,
                "bank_declined": 0.25,
                "network_error": 0.20,
                "payment_timeout": 0.15,
                "insufficient_funds": 0.05,
                "unknown": 0.05,
            },
        ),
        SegmentProfile(
            name="repeat_buyer",
            traffic_weight=0.10,
            min_amount_paise=200_000,  # ₹2,000
            max_amount_paise=1_200_000,  # ₹12,000
            device_types=("android", "ios", "web"),
            device_weights=(0.40, 0.30, 0.30),
            target_conversion_rate=0.67,
            payment_method_weights={
                "upi": 0.35,
                "card": 0.35,
                "netbanking": 0.20,
                "wallet": 0.10,
            },
            source_weights={
                "email": 0.40,
                "direct": 0.35,
                "organic": 0.15,
                "paid_search": 0.05,
                "social": 0.05,
            },
            failure_reason_weights={
                "bank_declined": 0.35,
                "authentication_failed": 0.25,
                "network_error": 0.20,
                "payment_timeout": 0.10,
                "insufficient_funds": 0.05,
                "unknown": 0.05,
            },
        ),
        SegmentProfile(
            name="ios_premium",
            traffic_weight=0.15,
            min_amount_paise=500_000,  # ₹5,000
            max_amount_paise=2_500_000,  # ₹25,000
            device_types=("ios",),
            device_weights=(1.0,),
            target_conversion_rate=0.74,
            payment_method_weights={
                "upi": 0.20,
                "card": 0.55,
                "netbanking": 0.20,
                "wallet": 0.05,
            },
            source_weights={
                "organic": 0.35,
                "paid_search": 0.25,
                "direct": 0.25,
                "social": 0.10,
                "email": 0.05,
            },
            failure_reason_weights={
                "authentication_failed": 0.35,
                "bank_declined": 0.30,
                "payment_timeout": 0.15,
                "network_error": 0.10,
                "insufficient_funds": 0.05,
                "unknown": 0.05,
            },
        ),
    ),
)
