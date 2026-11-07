"""Idempotent checkout handler: distributed lock + idempotency key dedup
+ graceful 409 Conflict, per Easy Rent Bali assessment Part 2."""
import hashlib
import json
import os
import time
from contextlib import contextmanager

try:
    import redis  # type: ignore
except ImportError:
    redis = None

LOCK_TTL_SECONDS = 10
IDEMPOTENCY_TTL_SECONDS = 3600


class ConflictError(Exception):
    """Raised when another checkout already holds the vehicle (HTTP 409)."""


class DuplicateRequestError(Exception):
    """Raised when an idempotency key was already processed (replay same response)."""


class InMemoryLockStore:
    """Fallback lock store. Good enough for the runnable concurrency test;
    use Redis in production so locks survive process restarts."""

    def __init__(self):
        self._locks = {}

    def acquire(self, key, ttl):
        now = time.monotonic()
        holder, expires = self._locks.get(key, (None, 0))
        if holder is not None and expires > now:
            return None
        token = hashlib.sha256(f"{key}:{now}".encode()).hexdigest()[:16]
        self._locks[key] = (token, now + ttl)
        return token

    def release(self, key, token):
        holder, _ = self._locks.get(key, (None, 0))
        if holder == token:
            del self._locks[key]

    def clear(self):
        self._locks.clear()


class InMemoryIdempotencyStore:
    def __init__(self):
        self._seen = {}

    def exists(self, key):
        return key in self._seen

    def put(self, key, response):
        self._seen[key] = response

    def clear(self):
        self._seen.clear()


class RedisLockStore:
    """Distributed lock via Redis SET NX EX. Safe across processes/hosts."""

    def __init__(self, client):
        self._r = client

    def acquire(self, key, ttl):
        token = hashlib.sha256(f"{key}:{time.monotonic()}".encode()).hexdigest()[:16]
        return token if self._r.set(f"lock:{key}", token, nx=True, ex=ttl) else None

    def release(self, key, token):
        # Check-and-delete only if we still own the lock.
        if self._r.get(f"lock:{key}") == token:
            self._r.delete(f"lock:{key}")

    def clear(self):
        for k in self._r.scan_iter("lock:*"):
            self._r.delete(k)


class RedisIdempotencyStore:
    def __init__(self, client):
        self._r = client

    def exists(self, key):
        return self._r.exists(f"idem:{key}") == 1

    def put(self, key, response):
        self._r.set(f"idem:{key}", json.dumps(response), ex=IDEMPOTENCY_TTL_SECONDS)

    def clear(self):
        for k in self._r.scan_iter("idem:*"):
            self._r.delete(k)


def default_stores():
    """Redis stores when REDIS_URL is set (compose), in-memory otherwise."""
    url = os.environ.get("REDIS_URL")
    if url and redis is not None:
        client = redis.Redis.from_url(url, decode_responses=True)
        return RedisLockStore(client), RedisIdempotencyStore(client)
    return InMemoryLockStore(), InMemoryIdempotencyStore()


@contextmanager
def vehicle_lock(store, vehicle_id):
    token = store.acquire(vehicle_id, LOCK_TTL_SECONDS)
    if token is None:
        raise ConflictError(f"vehicle {vehicle_id} is already being checked out")
    try:
        yield token
    finally:
        store.release(vehicle_id, token)


def checkout_vehicle(payload, lock_store, idempotency_store, confirm_hold, capture_payment):
    """Atomic checkout: lock vehicle -> confirm hold -> capture payment.

    confirm_hold(vehicle_id) -> bool: False means no confirmed hold (reject).
    capture_payment(payload) -> dict: payment confirmation.
    Returns checkout result dict. Raises ConflictError -> maps to HTTP 409.
    """
    idem_key = payload.get("idempotency_key")
    if not idem_key:
        raise ValueError("idempotency_key is required")
    idem_key = f"checkout:{idem_key}"

    if idempotency_store.exists(idem_key):
        raise DuplicateRequestError(idem_key)

    vehicle_id = payload["vehicle_id"]
    with vehicle_lock(lock_store, vehicle_id):
        if not confirm_hold(vehicle_id):
            raise ConflictError(f"no confirmed hold for vehicle {vehicle_id}")
        payment = capture_payment(payload)
        result = {
            "booking_id": payload["booking_id"],
            "vehicle_id": vehicle_id,
            "customer_id": payload["customer_id"],
            "payment": payment,
            "status": "confirmed",
        }
        idempotency_store.put(idem_key, result)
        return result


# ---- Runnable demo: 5 concurrent checkouts on car_avanza_04 ----

def _run_demo():
    import threading

    payload_base = {
        "booking_id": "erb_kuta_9941",
        "vehicle_id": "car_avanza_04",
        "customer_id": "cust_foreign_881",
    }
    lock_store, idem_store = default_stores()
    print(f"stores: {'Redis (REDIS_URL)' if os.environ.get('REDIS_URL') else 'in-memory (tanpa REDIS_URL)'}")
    holds = {"car_avanza_04"}  # only one confirmed hold exists
    captured = []
    results = []
    order = []

    def worker(i):
        payload = {**payload_base, "idempotency_key": f"req-{i}"}
        def confirm_hold(vid):
            time.sleep(0.01)  # simulate DB round-trip so threads genuinely contend
            return vid in holds
        def capture_payment(p):
            time.sleep(0.01)  # simulate payment gateway latency
            captured.append(p["booking_id"])
            return {"txn_id": f"txn_{i}", "amount": 350000}
        try:
            r = checkout_vehicle(payload, lock_store, idem_store, confirm_hold, capture_payment)
            results.append(("SUCCESS", i, r))
        except ConflictError as e:
            results.append(("CONFLICT_409", i, str(e)))
        except DuplicateRequestError:
            results.append(("DUPLICATE_REPLAY", i, None))
        finally:
            order.append(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for status, i, detail in sorted(results, key=lambda x: order.index(x[1])):
        print(f"  request req-{i}: {status}")

    successes = [r for r in results if r[0] == "SUCCESS"]
    conflicts = [r for r in results if r[0] == "CONFLICT_409"]
    assert len(successes) == 1, f"expected exactly 1 success, got {len(successes)}"
    assert len(conflicts) == 4, f"expected 4 conflicts, got {len(conflicts)}"
    assert len(captured) == 1, f"expected 1 payment capture, got {len(captured)}"
    print("\nPASS: exactly 1 success, 4 graceful 409 conflicts, 1 payment captured.")


if __name__ == "__main__":
    _run_demo()
