# AI Steering Audit

Verifiable evidence that the AI coding agent (OpenCode / GLM) produced flawed output during this assessment and how I steered it. Raw prompt history: see `raw_logs/` in this folder.

## Instance 1: Concurrent checkout test passed 5/5 successes (race not exercised)

**What happened:** The first version of `src/patch/test_concurrency.py` fired 5 threads but asserted `1 success, 4 conflicts`. It failed with `expected 1 success, got 5 SUCCESS`. The AI had also written an idempotency store whose `check_and_set(key, None)` stored `None` as the "in progress" marker, then treated `previous is not None` as the duplicate signal, so replays were never detected. On top of that, the critical section was so fast that threads rarely overlapped, hiding the race instead of proving the fix.

**Steering:**
1. Told the agent the exact failing assert output and asked it to fix the idempotency semantics first: split into `exists()` + `put()` so an in-flight request is distinguishable from a completed one.
2. Told the agent to widen the race window realistically instead of hacking sleeps around the lock: simulate DB and payment-gateway latency (10 ms) INSIDE `confirm_hold` / `capture_payment` callbacks, plus a `threading.Event` start gate so all 5 threads fire simultaneously.
3. Also caught the agent's follow-up bug myself: it used `time.sleep` in `test_concurrency.py` without importing `time` (NameError in all 5 threads). One-line fix: add the import.

**Result:** test now reliably shows `1 success, 4 graceful 409, 1 payment capture`.

## Instance 2: Dispatcher contained dead placeholder code

**What happened:** In `src/engine/dispatcher.py` the AI left a leftover lambda `flaky = lambda payload: random.random() < 0.0 or None  # placeholder replaced below` right above the real transport function. Dead code that would confuse any reviewer about which transport is actually used, and `random` would then be an unused import smell.

**Steering:** Instructed the agent to delete the placeholder line and keep only the simulated transport with a comment explaining what it simulates (endpoint recovering after 2 failed rounds). Also asked it to drop the misleading "flaky" naming.

**Result:** `dispatcher.py` has a single transport path and runs clean end-to-end, producing `output/incident_reconciliation.json`.

## General steering patterns used
- "Run it, show me the output" after every file: 3 of the AI's first versions did not run (missing import, failing assert, dead code). Logs-first verification caught all of them.
- Constraint reminders repeated each prompt: stdlib only, no external services, keep the exact file layout from the assessment PDF.
