from concurrent.futures import ThreadPoolExecutor

from app.models import PaymentRecord
from sqlalchemy.orm import Session
from tests.conftest import CountingGateway, body


def test_retry_returns_identical_response_and_single_charge(client, gateway, db_count):
    first = client.post("/payments", json=body())
    second = client.post("/payments", json=body())
    third = client.post("/payments", json=body())
    assert first.status_code == second.status_code == third.status_code == 200
    assert first.json() == second.json() == third.json()
    assert gateway.calls == 1
    assert db_count(client, "key-1") == 1


def test_failed_payment_is_replayed_not_recharged(make_client, db_count):
    gw = CountingGateway(failure_percent=0)
    client = make_client(gw)
    a = client.post("/payments", json=body(source="tok_declined"))
    b = client.post("/payments", json=body(source="tok_declined"))
    assert a.json() == b.json() and a.json()["status"] == "failed"
    assert gw.calls == 1 and db_count(client) == 1


def test_different_keys_create_different_payments(client, gateway, db_count):
    a = client.post("/payments", json=body(idempotency_key="a")).json()
    b = client.post("/payments", json=body(idempotency_key="b")).json()
    assert a["payment_id"] != b["payment_id"]
    assert gateway.calls == 2 and db_count(client) == 2


def test_same_key_different_payload_rejected(client, gateway, db_count):
    client.post("/payments", json=body())
    r = client.post("/payments", json=body(amount=9999))
    assert r.status_code == 422
    assert gateway.calls == 1 and db_count(client) == 1


def test_concurrent_requests_same_key_single_payment(make_client, db_count):
    gw = CountingGateway(delay=0.3, failure_percent=0)
    client = make_client(gw)
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: client.post("/payments", json=body(idempotency_key="race")), range(20)))
    assert all(r.status_code == 200 for r in results)
    assert len({r.json()["payment_id"] for r in results}) == 1
    assert len({str(r.json()) for r in results}) == 1
    assert gw.calls == 1
    assert db_count(client, "race") == 1


def test_concurrent_distinct_keys_all_processed(make_client, db_count):
    gw = CountingGateway(failure_percent=0)
    client = make_client(gw)
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda i: client.post("/payments", json=body(idempotency_key=f"k{i}")), range(10)))
    assert all(r.status_code == 200 for r in results)
    assert db_count(client) == 10 and gw.calls == 10


def test_gateway_error_releases_key_so_retry_works(make_client, db_count):
    gw = CountingGateway(failure_percent=0)
    client = make_client(gw)
    r = client.post("/payments", json=body(source="tok_error"))
    assert r.status_code == 502
    assert db_count(client) == 0
    # Same key, now with a healthy source -> processed normally.
    ok = client.post("/payments", json=body(source="tok_visa"))
    assert ok.status_code == 200 and ok.json()["status"] == "succeeded"
    assert db_count(client) == 1


def test_in_progress_timeout_returns_409(make_client):
    client = make_client(CountingGateway(failure_percent=0), in_progress_timeout=0.2)
    from app.service import fingerprint
    from app.schemas import PaymentRequest
    req = body(idempotency_key="stuck")
    with Session(client.app.state.engine) as s:
        s.add(PaymentRecord(id="p1", idempotency_key="stuck", fingerprint=fingerprint(PaymentRequest(**req)),
                            amount=1500, currency="USD", source="tok_visa", status="processing"))
        s.commit()
    r = client.post("/payments", json=req)
    assert r.status_code == 409 and r.headers["Retry-After"] == "1"


def test_waiter_picks_up_key_released_after_gateway_error(make_client, db_count):
    """A waiting duplicate re-processes if the winner's gateway call failed."""
    import threading, time

    class FlakyGateway(CountingGateway):
        def charge(self, key, amount, currency, source):
            time.sleep(0.3)
            with threading.Lock():
                self.calls += 1
                if self.calls == 1:
                    from app.gateway import GatewayError
                    raise GatewayError("first call fails")
            return True

    client = make_client(FlakyGateway(failure_percent=0))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(client.post, "/payments", json=body(idempotency_key="flaky")) for _ in range(2)]
        codes = sorted(f.result().status_code for f in futs)
    assert codes == [200, 502] or codes == [200, 200]
    assert db_count(client, "flaky") == 1
