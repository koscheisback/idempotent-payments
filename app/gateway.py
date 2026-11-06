"""Deterministic simulated payment gateway (no external calls)."""
import hashlib


class GatewayError(Exception):
    """Gateway unreachable / internal error (transient; safe to retry)."""


class SimulatedGateway:
    """Outcome depends only on (seed, idempotency_key) so it is reproducible.

    Magic sources: ``tok_declined`` always declines, ``tok_error`` always raises.
    """

    def __init__(self, seed: int = 42, failure_percent: int = 10):
        if not 0 <= failure_percent <= 100:
            raise ValueError("failure_percent must be within 0..100")
        self.seed = seed
        self.failure_percent = failure_percent

    def charge(self, idempotency_key: str, amount: int, currency: str, source: str) -> bool:
        """Return True if the charge succeeded, False if declined."""
        if source == "tok_error":
            raise GatewayError("simulated gateway outage")
        if source == "tok_declined":
            return False
        digest = hashlib.sha256(f"{self.seed}:{idempotency_key}".encode()).digest()
        return int.from_bytes(digest[:4], "big") % 100 >= self.failure_percent
