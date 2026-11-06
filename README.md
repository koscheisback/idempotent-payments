# Idempotent Payments Service

A small FastAPI + PostgreSQL service providing **exactly-once** payment processing via idempotency keys.

## Run (Docker)

```bash
docker compose up --build
```
API: `http://localhost:8080` · Health: `GET /health` · Docs: `/docs`

```bash
curl -X POST localhost:8080/payments -H 'Content-Type: application/json' \
  -d '{"idempotency_key":"order-123","amount":1500,"currency":"USD","source":"tok_visa"}'
```
Repeat the same call: you get the identical `payment_id`/`status`, and no second charge.

## API

`POST /payments` body: `idempotency_key` (str), `amount` (positive int, minor units), `currency` (ISO-4217 style, e.g. `USD`), `source` (str).

| Code | Meaning |
|------|---------|
| 200 | `{payment_id, status, amount, currency}`; `status` is `succeeded` or `failed` (declined). Replays return the stored result |
| 422 | Invalid input, or key reused with a **different** payload |
| 409 | Original request with this key is still processing (`Retry-After: 1`) |
| 502 | Gateway outage; nothing stored, retry with the same key |

`GET /health` -> `200 {"status":"ok"}` or `503` if the DB is unreachable.

Simulated gateway: outcome is a deterministic hash of `(seed, idempotency_key)`; `GATEWAY_FAILURE_PERCENT` of keys are declined. Magic sources: `tok_declined` (always declined), `tok_error` (gateway outage).

## Design

- `payments.idempotency_key` has a **UNIQUE** constraint.
- Each request first `INSERT`s a row with status `processing`. The DB guarantees only one concurrent request wins; the winner alone calls the gateway, then updates the row.
- Losers (concurrent or later retries) read the row; if still `processing` they poll until done (timeout -> 409), then return the stored result.
- A gateway outage deletes the claim row so the client may retry safely; waiting duplicates then re-process.
- A request fingerprint (amount/currency/source) detects key reuse with different data.
- Every request is logged (method, path, status, latency); errors are logged with details.

## Configuration (env)

`DATABASE_URL`, `GATEWAY_SEED` (42), `GATEWAY_FAILURE_PERCENT` (10), `IN_PROGRESS_TIMEOUT` (10s), `POLL_INTERVAL` (0.05s).

## Tests (local)

Tests use SQLite, so no database is required.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```
Coverage is enforced at >= 90% (`pytest.ini`).

## Layout

```
app/  config.py db.py models.py schemas.py gateway.py service.py main.py
tests/ test_unit.py test_integration.py conftest.py
```
