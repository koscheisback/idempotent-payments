"""Request / response schemas with input validation."""
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class PaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=255)
    amount: StrictInt = Field(gt=0, le=1_000_000_000, description="Amount in minor units (e.g. cents)")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    source: str = Field(min_length=1, max_length=255)

    @field_validator("idempotency_key", "source")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v


class PaymentResponse(BaseModel):
    payment_id: str
    status: str
    amount: int
    currency: str
