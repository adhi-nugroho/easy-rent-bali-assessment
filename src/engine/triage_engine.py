"""Triage engine: flag fleet telemetry per Easy Rent Bali assessment rules."""
import json
import sys
from pathlib import Path

RESTRICTED_ZONES = {"RESTRICTED_OFFROAD_BATUKARU"}
OFFLINE_THRESHOLD_SECONDS = 15 * 60  # > 15 mins silent
BATTERY_MIN_VOLTAGE = 11.5
FUEL_MIN_PERCENT = 15


def triage(cars):
    alerts = []
    for car in cars:
        reasons = []

        # 🚨 CRITICAL_SECURITY_ALERT
        security = []
        if car["status"] == "available" and car["current_speed_kmh"] > 0:
            security.append(
                f"available car moving at {car['current_speed_kmh']} km/h"
            )
        if car["current_location"]["zone"] in RESTRICTED_ZONES:
            security.append(f"in restricted zone {car['current_location']['zone']}")
        if security:
            reasons.append(("CRITICAL_SECURITY_ALERT", "; ".join(security)))

        # 📡 OFFLINE_TELEMETRY_DROP
        if car["status"] == "rented" and car["last_ping_seconds_ago"] > OFFLINE_THRESHOLD_SECONDS:
            reasons.append((
                "OFFLINE_TELEMETRY_DROP",
                f"rented car silent for {car['last_ping_seconds_ago']}s (> {OFFLINE_THRESHOLD_SECONDS}s)",
            ))

        # ⚠️ MAINTENANCE_REQUIRED
        maintenance = []
        if car["battery_voltage"] < BATTERY_MIN_VOLTAGE:
            maintenance.append(f"battery {car['battery_voltage']}V (< {BATTERY_MIN_VOLTAGE}V)")
        if car["fuel_percent"] < FUEL_MIN_PERCENT:
            maintenance.append(f"fuel {car['fuel_percent']}% (< {FUEL_MIN_PERCENT}%)")
        if maintenance:
            reasons.append(("MAINTENANCE_REQUIRED", "; ".join(maintenance)))

        for tag, reason in reasons:
            alerts.append({
                "alert": tag,
                "device_id": car["device_id"],
                "plate": car["plate"],
                "model": car["model"],
                "zone": car["current_location"]["zone"],
                "gateway_id": car["gateway_id"],
                "reason": reason,
            })
    return alerts


def main():
    data_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parents[2] / "data" / "outage_telemetry.json"
    cars = json.loads(data_path.read_text())
    alerts = triage(cars)
    for a in alerts:
        print(f"{a['alert']}: {a['plate']} ({a['device_id']}) - {a['reason']}")
    print(f"\nTotal alerts: {len(alerts)} from {len(cars)} vehicles")


if __name__ == "__main__":
    main()
