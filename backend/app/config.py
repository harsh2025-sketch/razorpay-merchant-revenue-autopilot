from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment variables and .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    APP_ENV: str = "development"
    DATABASE_URL: str = "sqlite:///./data/autopilot.db"
    CORS_ALLOWED_ORIGINS: str = "http://localhost:3000"
    # Backward-compatible alias for local .env files created before Task 18A.
    CORS_ORIGINS: str | None = None

    # Razorpay execution is real by default. The hosted demo may explicitly opt
    # into a clearly-labelled simulated boundary when merchant KYC credentials
    # are unavailable. Simulated mode never sends HTTP traffic to Razorpay.
    RAZORPAY_EXECUTION_MODE: Literal["real", "simulated"] = "real"
    RAZORPAY_KEY_ID: str | None = None
    RAZORPAY_KEY_SECRET: str | None = None
    # Optional: an Offer already created in the Razorpay Dashboard, used only
    # by scripts/verify_razorpay.py. Offers are never created via the API.
    RAZORPAY_TEST_OFFER_ID: str | None = None

    OPENAI_API_KEY: str | None = None
    # Model used by the AI diagnosis engine (Task 08); configurable per environment.
    OPENAI_MODEL: str = "gpt-4.1-mini"

    @model_validator(mode="after")
    def _configure_simulated_razorpay_credentials(self) -> "Settings":
        """Provide internal non-secret sentinels only for explicit simulation.

        The existing executor validates that credential fields are present
        before constructing the Razorpay client. In simulated mode those
        values are intentionally local sentinels so the same safe idempotency
        path can run without real merchant credentials. Real mode retains the
        original fail-closed requirement for actual Test Mode keys.
        """
        if self.RAZORPAY_EXECUTION_MODE == "simulated":
            if not self.RAZORPAY_KEY_ID:
                self.RAZORPAY_KEY_ID = "demo_simulated_key"
            if not self.RAZORPAY_KEY_SECRET:
                self.RAZORPAY_KEY_SECRET = "demo_simulated_secret"
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
