import pytest

from app.config import Settings
from app.gateway import GatewayError, SimulatedGateway
from app.schemas import PaymentRequest
from app.service import fingerprint
from tests.conftest import body


def test_gateway_is_deterministic():
    g1, g2 = SimulatedGateway(seed=7, failure_percent=50), SimulatedGateway(seed=7, failure_percent=50)
    keys = [f"k{i}" for i in range(50)]
    assert [g1.charge(k, 1, "USD", "tok") for k in keys] == [g2.charge(k, 1, "USD", "tok") for k in keys]


def test_gateway_failure_extremes():
    assert SimulatedGateway(failure_percent=0).charge("a", 1, "USD", "tok") is True
    assert SimulatedGateway(failure_percent=100).charge("a", 1, "USD", "tok") is False


def test_gateway_magic_sources():
    g = SimulatedGateway(failure_percent=0)
    assert g.charge("a", 1, "USD", "tok_declined") is False
    with pytest.raises(GatewayError):
        g.charge("a", 1, "USD", "tok_error")


def test_gateway_rejects_bad_percent():
    with pytest.raises(ValueError):
        SimulatedGateway(failure_percent=101)


def test_fingerprint_differs_on_payload():
    a = PaymentRequest(**body())
    assert fingerprint(a) == fingerprint(PaymentRequest(**body()))
    assert fingerprint(a) != fingerprint(PaymentRequest(**body(amount=1)))


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///x.db")
    monkeypatch.setenv("GATEWAY_SEED", "5")
    s = Settings.from_env()
    assert s.database_url == "sqlite:///x.db" and s.gateway_seed == 5


# ---- endpoint unit tests ----
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_health_unhealthy_when_db_down(client):
    client.app.state.engine.dispose()
    from sqlalchemy.pool import NullPool
    import app.main as m
    original = client.app.state.engine.connect
    client.app.state.engine.connect = lambda: (_ for _ in ()).throw(RuntimeError("down"))
    try:
        assert client.get("/health").status_code == 503
    finally:
        client.app.state.engine.connect = original


def test_create_payment_returns_expected_fields(client):
    r = client.post("/payments", json=body())
    assert r.status_code == 200
    data = r.json()
    assert set(data) == {"payment_id", "status", "amount", "currency"}
    assert data["status"] == "succeeded" and data["amount"] == 1500 and data["currency"] == "USD"


def test_declined_payment_status_failed(client):
    r = client.post("/payments", json=body(source="tok_declined"))
    assert r.status_code == 200 and r.json()["status"] == "failed"


@pytest.mark.parametrize("override", [
    {"idempotency_key": ""}, {"idempotency_key": "   "}, {"amount": 0}, {"amount": -5},
    {"amount": 10.5}, {"amount": "100"}, {"amount": True}, {"currency": "usd"}, {"currency": "US"},
    {"source": ""}, {"source": "  "}, {"extra": 1},
])
def test_validation_errors(client, override):
    assert client.post("/payments", json=body(**override)).status_code == 422


def test_missing_fields(client):
    assert client.post("/payments", json={}).status_code == 422


def test_unhandled_error_returns_500(client):
    client.app.state.service.process = lambda req: (_ for _ in ()).throw(RuntimeError("boom"))
    client2 = type(client)(client.app, raise_server_exceptions=False)
    assert client2.post("/payments", json=body()).status_code == 500
