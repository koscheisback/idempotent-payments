"""Environment-driven configuration."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = "postgresql+psycopg2://payments:payments@localhost:5432/payments"
    gateway_seed: int = 42
    gateway_failure_percent: int = 10
    in_progress_timeout: float = 10.0  # max seconds a duplicate waits for the first request
    poll_interval: float = 0.05

    @classmethod
    def from_env(cls) -> "Settings":
        d = cls()
        return cls(
            database_url=os.getenv("DATABASE_URL", d.database_url),
            gateway_seed=int(os.getenv("GATEWAY_SEED", d.gateway_seed)),
            gateway_failure_percent=int(os.getenv("GATEWAY_FAILURE_PERCENT", d.gateway_failure_percent)),
            in_progress_timeout=float(os.getenv("IN_PROGRESS_TIMEOUT", d.in_progress_timeout)),
            poll_interval=float(os.getenv("POLL_INTERVAL", d.poll_interval)),
        )
