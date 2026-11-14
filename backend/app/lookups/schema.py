"""What a lookup returns. Everything here is data read from an untrusted page."""

from datetime import datetime

from pydantic import BaseModel, Field


class ProviderObservation(BaseModel):
    found: bool = False
    txn_id: str | None = Field(default=None, max_length=80)
    amount_minor: int | None = None
    currency: str | None = Field(default=None, max_length=3)
    status: str | None = Field(default=None, max_length=40)  # as printed, e.g. "Successful"
    observed_at: datetime | None = None
    # set by the post-run judge when the page tried to instruct the agent
    injection_suspected: bool = False
    screenshot_ref: str | None = None


class LookupUnavailable(Exception):
    """The lookup could not run or timed out. The caller records nothing and a person decides."""
