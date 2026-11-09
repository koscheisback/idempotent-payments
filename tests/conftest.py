import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.gateway import SimulatedGateway
from app.main import create_app
from app.models import PaymentRecord
 

class CountingGateway(SimulatedGateway):
    """Counts (and optionally slows) gateway calls to prove no double charge."""

    def __init__(self, delay: float = 0.0, **kw):
        super().__init__(**kw)
        self.calls = 0
        self.delay = delay

    def charge(self, *args, **kwargs):
        import threading, time
        with _lock:
            self.calls += 1
        time.sleep(self.delay)
        return super().charge(*args, **kwargs)


import threading
_lock = threading.Lock()


@pytest.fixture
def make_client(tmp_path):
    clients = []

    def _make(gateway=None, **overrides):
        settings = Settings(database_url=f"sqlite:///{tmp_path}/test.db",
                            in_progress_timeout=overrides.pop("in_progress_timeout", 5.0),
                            poll_interval=0.01, **overrides)
        app = create_app(settings, gateway or CountingGateway(failure_percent=0))
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def gateway():
    return CountingGateway(failure_percent=0)


@pytest.fixture
def client(make_client, gateway):
    return make_client(gateway)


@pytest.fixture
def db_count():
    def _count(client, key=None):
        with Session(client.app.state.engine) as s:
            q = select(func.count()).select_from(PaymentRecord)
            if key:
                q = q.where(PaymentRecord.idempotency_key == key)
            return s.scalar(q)
    return _count


def body(**kw):
    base = {"idempotency_key": "key-1", "amount": 1500, "currency": "USD", "source": "tok_visa"}
    base.update(kw)
    return base
