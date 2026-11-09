"""FastAPI application factory."""
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request 
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import Settings
from app.db import init_schema, make_engine, make_session_factory
from app.gateway import SimulatedGateway
from app.schemas import PaymentRequest, PaymentResponse
from app.service import GatewayUnavailable, IdempotencyConflict, PaymentInProgress, PaymentService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("payments.api")


def create_app(settings: Optional[Settings] = None, gateway: Optional[SimulatedGateway] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)
    gateway = gateway or SimulatedGateway(settings.gateway_seed, settings.gateway_failure_percent)
    service = PaymentService(make_session_factory(engine), gateway,
                             settings.in_progress_timeout, settings.poll_interval)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        init_schema(engine)
        yield
        engine.dispose()

    app = FastAPI(title="Idempotent Payments", lifespan=lifespan)
    app.state.service = service
    app.state.engine = engine

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error %s %s", request.method, request.url.path)
            return JSONResponse({"detail": "internal server error"}, status_code=500)
        log.info("%s %s -> %d (%.1f ms)", request.method, request.url.path, response.status_code,
                 (time.perf_counter() - start) * 1000)
        return response

    @app.post("/payments", response_model=PaymentResponse, status_code=200)
    def create_payment(payload: PaymentRequest):
        try:
            return service.process(payload)
        except IdempotencyConflict as exc:
            return JSONResponse({"detail": str(exc)}, status_code=422)
        except PaymentInProgress as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409, headers={"Retry-After": "1"})
        except GatewayUnavailable:
            return JSONResponse({"detail": "payment gateway unavailable, please retry"}, status_code=502)

    @app.get("/health")
    def health():
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:
            log.error("health check failed: %s", exc)
            return JSONResponse({"status": "unhealthy"}, status_code=503)
        return {"status": "ok"}

    return app
