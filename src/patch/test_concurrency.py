"""Concurrency test: 5 simultaneous checkout requests on car_avanza_04.
Expected: exactly 1 success, 4 graceful 409 conflicts.

Run: python3 test_concurrency.py
"""
import threading
import time

from checkout_handler import (
    ConflictError,
    DuplicateRequestError,
    default_stores,
    checkout_vehicle,
)

VEHICLE = "car_avanza_04"


def test_five_simultaneous_requests():
    lock_store, idem_store = default_stores()
    lock_store.clear()  # idempotent reruns: buang state run sebelumnya
    idem_store.clear()
    holds = {VEHICLE}  # DB state: exactly one confirmed vehicle hold
    captured_payments = []
    results = []
    start_gate = threading.Event()

    def worker(i):
        payload = {
            "booking_id": "erb_kuta_9941",
            "vehicle_id": VEHICLE,
            "customer_id": "cust_foreign_881",
            "idempotency_key": f"req-{i}",
        }

        def confirm_hold(vid):
            time.sleep(0.01)  # simulate DB round-trip: widens the race window
            return vid in holds  # DB check inside the lock

        def capture_payment(p):
            time.sleep(0.01)  # simulate payment gateway latency
            captured_payments.append(p["booking_id"])
            return {"txn_id": f"txn_{i}"}

        start_gate.wait()  # maximize contention: all threads fire together
        try:
            r = checkout_vehicle(payload, lock_store, idem_store, confirm_hold, capture_payment)
            results.append(("SUCCESS", i))
        except ConflictError:
            results.append(("CONFLICT_409", i))
        except DuplicateRequestError:
            results.append(("DUPLICATE_REPLAY", i))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    start_gate.set()
    for t in threads:
        t.join()

    statuses = [s for s, _ in results]
    assert statuses.count("SUCCESS") == 1, f"expected 1 success, got {statuses}"
    assert statuses.count("CONFLICT_409") == 4, f"expected 4 conflicts, got {statuses}"
    assert len(captured_payments) == 1, f"payment captured {len(captured_payments)}x, must be exactly 1"
    print("PASS: 5 concurrent checkouts -> 1 success, 4 graceful 409, 1 payment capture.")
    return results


def test_duplicate_idempotency_key_replay():
    lock_store, idem_store = default_stores()
    lock_store.clear()
    idem_store.clear()

    def confirm_hold(vid):
        return vid in {VEHICLE}

    def capture_payment(p):
        return {"txn_id": "txn_once"}

    payload = {
        "booking_id": "erb_kuta_9941",
        "vehicle_id": VEHICLE,
        "customer_id": "cust_foreign_881",
        "idempotency_key": "same-key",
    }

    first = checkout_vehicle(payload, lock_store, idem_store, confirm_hold, capture_payment)
    try:
        checkout_vehicle(payload, lock_store, idem_store, confirm_hold, capture_payment)
        raise AssertionError("replay with same idempotency key must be rejected")
    except DuplicateRequestError:
        pass
    print("PASS: duplicate idempotency key replays do not double-charge.")


if __name__ == "__main__":
    test_five_simultaneous_requests()
    test_duplicate_idempotency_key_replay()
