"""Idempotent payment business logic.

Concurrency strategy: the first thing we do is INSERT a row in state
``processing``. The UNIQUE constraint on ``idempotency_key`` makes this an
atomic claim - exactly one concurrent request wins. Losers wait for the winner
to finish and then return the stored result. The gateway is only ever called
by the winner.
"""
import hashlib
import logging
import time
import uuid

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.gateway import GatewayError, SimulatedGateway
from app.models import STATUS_FAILED, STATUS_PROCESSING, STATUS_SUCCEEDED, PaymentRecord
from app.schemas import PaymentRequest, PaymentResponse

log = logging.getLogger("payments.service")


class IdempotencyConflict(Exception):
    """Same key reused with a different payload."""


class PaymentInProgress(Exception):
    """Original request with this key is still running."""


class GatewayUnavailable(Exception):
    """Gateway failed; nothing was stored, client may retry."""


def fingerprint(req: PaymentRequest) -> str:
    raw = f"{req.amount}|{req.currency}|{req.source}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _to_response(rec: PaymentRecord) -> PaymentResponse:
    return PaymentResponse(payment_id=rec.id, status=rec.status, amount=rec.amount, currency=rec.currency)


class PaymentService:
    def __init__(self, session_factory: sessionmaker, gateway: SimulatedGateway,
                 in_progress_timeout: float = 10.0, poll_interval: float = 0.05):
        self._sf = session_factory
        self._gateway = gateway
        self._timeout = in_progress_timeout
        self._poll = poll_interval

    def process(self, req: PaymentRequest) -> PaymentResponse:
        fp = fingerprint(req)
        payment_id = str(uuid.uuid4())
        with self._sf() as session:
            session.add(PaymentRecord(
                id=payment_id, idempotency_key=req.idempotency_key, fingerprint=fp,
                amount=req.amount, currency=req.currency, source=req.source,
                status=STATUS_PROCESSING,
            ))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                log.info("replay detected key=%s", req.idempotency_key)
                return self._resolve_existing(req, fp)
        return self._execute(payment_id, req)

    def _execute(self, payment_id: str, req: PaymentRequest) -> PaymentResponse:
        try:
            ok = self._gateway.charge(req.idempotency_key, req.amount, req.currency, req.source)
        except GatewayError as exc:
            # Release the key so the client can safely retry.
            with self._sf() as session:
                session.execute(delete(PaymentRecord).where(PaymentRecord.id == payment_id))
                session.commit()
            log.error("gateway error key=%s: %s", req.idempotency_key, exc)
            raise GatewayUnavailable(str(exc)) from exc

        with self._sf() as session:
            rec = session.get(PaymentRecord, payment_id)
            rec.status = STATUS_SUCCEEDED if ok else STATUS_FAILED
            session.commit()
            log.info("payment %s key=%s status=%s", rec.id, req.idempotency_key, rec.status)
            return _to_response(rec)

    def _resolve_existing(self, req: PaymentRequest, fp: str) -> PaymentResponse:
        deadline = time.monotonic() + self._timeout
        while True:
            with self._sf() as session:
                rec = session.scalar(
                    select(PaymentRecord).where(PaymentRecord.idempotency_key == req.idempotency_key))
            if rec is None:  # winner released the key after a gateway error
                return self.process(req)
            if rec.fingerprint != fp:
                raise IdempotencyConflict("idempotency_key was already used with different parameters")
            if rec.status != STATUS_PROCESSING:
                return _to_response(rec)
            if time.monotonic() >= deadline:
                raise PaymentInProgress("original request is still being processed")
            time.sleep(self._poll)
