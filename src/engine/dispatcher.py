"""Resilient dispatcher: format alerts, retry with exponential backoff + jitter,
and write reconciliation report to output/incident_reconciliation.json."""
import json
import random
import sys
import time
from pathlib import Path

from triage_engine import triage

MAX_ATTEMPTS = 4
BASE_DELAY = 0.2  # seconds, scaled down so the demo runs fast
MAX_DELAY = 5.0


def format_payload(alert):
    return {
        "severity": "CRITICAL" if alert["alert"] == "CRITICAL_SECURITY_ALERT" else "WARNING",
        "tag": alert["alert"],
        "payload": alert,
        "message": f"[{alert['alert']}] {alert['plate']} @ {alert['zone']}: {alert['reason']}",
    }


def send(payload, transport):
    """transport(payload) -> bool (True = delivered). Raises on hard failure."""
    return transport(payload)


def dispatch(alerts, transport):
    """Deliver each alert with exponential backoff + jitter.
    Returns list of {alert_id, attempts, delivered}."""
    results = []
    for alert in alerts:
        payload = format_payload(alert)
        delivered = False
        attempts = 0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            attempts = attempt
            try:
                if send(payload, transport):
                    delivered = True
                    break
            except Exception:
                pass  # transient transport error, retry
            delay = min(BASE_DELAY * (2 ** (attempt - 1)), MAX_DELAY)
            time.sleep(delay + random.uniform(0, delay / 2))  # backoff + jitter
        results.append({
            "alert_id": f"{alert['device_id']}:{alert['alert']}",
            "plate": alert["plate"],
            "tag": alert["alert"],
            "attempts": attempts,
            "delivered": delivered,
        })
    return results


def reconcile(alerts, delivery_results):
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "summary": {
            "vehicles_scanned": len({a["device_id"] for a in alerts}) if alerts else 0,
            "alerts_raised": len(alerts),
            "delivered": sum(1 for r in delivery_results if r["delivered"]),
            "failed": sum(1 for r in delivery_results if not r["delivered"]),
        },
        "alerts": alerts,
        "delivery": delivery_results,
    }


def main():
    data_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parents[2] / "data" / "outage_telemetry.json"
    out_path = Path(__file__).parents[2] / "output" / "incident_reconciliation.json"

    cars = json.loads(data_path.read_text())
    alerts = triage(cars)

    # Simulated transport: endpoint recovers after 2 failed rounds (WAN2 instability).
    def transport(payload):
        # Simulates dispatcher endpoint recovering after 2 failed rounds.
        if not hasattr(transport, "calls"):
            transport.calls = {}
        key = payload["payload"]["device_id"]
        transport.calls[key] = transport.calls.get(key, 0) + 1
        return transport.calls[key] >= 3

    delivery = dispatch(alerts, transport)
    report = reconcile(alerts, delivery)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(f"Reconciliation written to {out_path}")
    for r in delivery:
        print(f"  {r['alert_id']}: attempts={r['attempts']} delivered={r['delivered']}")


if __name__ == "__main__":
    main()
