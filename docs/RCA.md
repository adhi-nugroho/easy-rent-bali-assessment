# RCA: Kiosk Checkout Deadlock & Duplicate Payment Captures

Incident: `2026-10-29T18:32:14.218+08:00` - `CheckoutWorker` `TransactionRollbackError: deadlock detected`
Payload: `{"booking_id": "erb_kuta_9941", "vehicle_id": "car_avanza_04", "customer_id": "cust_foreign_881"}`

## Timeline
1. Fiber cut at Kuta Hub; gateway failed over to cellular WAN2 with > 400 ms jitter and 18% packet loss.
2. Kiosk requests to the checkout API timed out. Kiosk firmware auto-retried the same checkout with **new requests** (no idempotency key), several times per customer tap.
3. Multiple retried requests ran concurrently for the same vehicle (`car_avanza_04`).

## Root cause 1: why retries caused transactional deadlocks
The checkout handler wrapped "reserve vehicle row" and "insert into `vehicle_reservations`" in one DB transaction, and took row locks in an inconsistent order:

- Process A (request #1): locks row in `vehicles` first, then waits to lock `vehicle_reservations`.
- Process B (request #2, the retry): already inserted into `vehicle_reservations`, then waits to lock the same `vehicles` row.

Each process holds a lock the other needs: a classic **circular wait** deadlock. PostgreSQL detects it and kills one transaction (`deadlock detected`), which then cascades as `TransactionRollbackError` in the worker. Under normal latency the overlap window was milliseconds so it surfaced rarely; WAN2's jitter stretched every round-trip, so retries piled up and the window became near-permanent.

## Root cause 2: how payments were captured without confirmed vehicle holds
Capture-payment and hold-confirmation were **not atomic**:

1. Request arrives -> payment gateway is called (slow on WAN2, 5-10 s).
2. Retry #2 arrives while #1 is still waiting on the payment gateway. Both pass the "is vehicle available?" check (both read `available` before either wrote `reserved`).
3. Both proceed to capture the card -> **duplicate credit card captures**.
4. When the reservation transaction then deadlocked and rolled back, the payment capture was a separate non-transactional side effect that did NOT roll back -> payment captured while the vehicle hold was never confirmed, and the car stayed "locked" (unbookable) in inventory.

In short: the money path and the inventory path had no shared atomicity, and the retry storm exploited the gap.

## Fix (implemented in `src/patch/checkout_handler.py`)
1. **Distributed lock per vehicle** (`vehicle_lock`, TTL-based, Redis-compatible interface): only one checkout for a given `vehicle_id` may execute at a time. Second concurrent request fails fast with `ConflictError` -> HTTP **409 Conflict**, no payment call is made.
2. **Idempotency key deduplication**: every checkout must send `idempotency_key`; replays of the same key are rejected before any work (`DuplicateRequestError`), so kiosk-level retries can never double-capture.
3. **Hold confirmed inside the lock, before capture**: `confirm_hold(vehicle_id)` runs inside the exclusive lock; if there is no confirmed hold, we raise 409. Payment capture happens strictly after a confirmed hold, inside the same critical section.
4. **Graceful 409 contract**: kiosks treat 409 as terminal (do not blind-retry), and duplicates return the original recorded result.

## Verification
`src/patch/test_concurrency.py` fires 5 simultaneous checkout threads for `car_avanza_04`:

```
PASS: 5 concurrent checkouts -> 1 success, 4 graceful 409, 1 payment capture.
PASS: duplicate idempotency key replays do not double-charge.
```

Exactly 1 success + 4 graceful conflicts, and only 1 payment captured, matching the requirement.

## Follow-ups (out of scope of the patch)
- Migrate the in-memory lock store to Redis (already abstracted: `InMemoryLockStore` -> `redis` client) so locks survive process restarts.
- Add `SELECT ... FOR UPDATE` on the reservation row as DB-level belt-and-braces once the real schema is available.
- Fix kiosk firmware to retry with the SAME idempotency key and honor 409 as terminal.
