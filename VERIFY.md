# Verification Checklist

Cara membuktikan semua deliverable berjalan dan sesuai requirement. Semua command dijalankan dari folder `easy-rent-bali-assessment/`.

## 1. Part 2: Concurrency test (requirement: 5 request simultaneous, tepat 1 sukses 4 conflict)

```bash
podman run --rm -v "$PWD":/app -w /app docker.io/library/python:3.12-slim python3 src/patch/test_concurrency.py
```

Harapan:
```
PASS: 5 concurrent checkouts -> 1 success, 4 graceful 409, 1 payment capture.
PASS: duplicate idempotency key replays do not double-charge.
```

Yang dibuktikan:
- **Tepat 1 SUCCESS** dari 5 thread yang start bersamaan (`start_gate` Event).
- **4 x CONFLICT_409**, bukan crash/timeout/500.
- **Payment hanya di-capture 1x** (assert `len(captured_payments) == 1`), ini yang mencegah duplicate credit card capture seperti di incident.
- **Replay idempotency key yang sama ditolak**, tidak double-charge.

Untuk memastikan test-nya benar-benar mendeteksi race (bukan pass palsu), coba juga hapus lock-nya dan lihat test GAGAL:
1. Edit `src/patch/checkout_handler.py`, di `checkout_vehicle` ganti `with vehicle_lock(lock_store, vehicle_id):` jadi `with contextlib.nullcontext():`
2. Jalankan test lagi. Harapan: FAIL dengan `payment captured 2x` atau lebih, karena tanpa lock dua request lolos bareng. Ini membuktikan lock-lah yang menahan race.
3. Kembalikan kodenya.

## 2. Part 2: Patched handler demo

```bash
podman run --rm -v "$PWD":/app -w /app docker.io/library/python:3.12-slim python3 src/patch/checkout_handler.py
```

Harapan: 4 baris `CONFLICT_409`, 1 baris `SUCCESS`, lalu `PASS: exactly 1 success, 4 graceful 409 conflicts, 1 payment captured.`

## 3. Part 3: Triage engine (requirement: 3 kategori alert sesuai rule)

```bash
podman run --rm -v "$PWD":/app -w /app docker.io/library/python:3.12-slim python3 src/engine/triage_engine.py
```

Harapan persis (6 alert dari 8 mobil, 2 mobil bersih):

| Mobil | Alert | Alasan |
|---|---|---|
| DK 5678 CD (iot_car_02) | CRITICAL_SECURITY_ALERT | available tapi jalan 52 km/h |
| DK 7890 IJ (iot_car_05) | CRITICAL_SECURITY_ALERT | di zona restricted RESTRICTED_OFFROAD_BATUKARU |
| DK 2345 KL (iot_car_06) | OFFLINE_TELEMETRY_DROP | rented diam 1320s > 900s (15 menit) |
| DK 9012 EF (iot_car_03) | MAINTENANCE_REQUIRED | battery 11.2V < 11.5V |
| DK 3456 GH (iot_car_04) | MAINTENANCE_REQUIRED | fuel 9% < 15% |
| DK 4567 OP (iot_car_08) | MAINTENANCE_REQUIRED | battery 11.3V + fuel 11% |

Harus TIDAK muncul alert untuk iot_car_01 (rented, normal), iot_car_07 (available, diam, normal).

Cek edge rule: iot_car_05 rented bergerak 60 km/j TIDAK kena OFFLINE (ping 80s < 900s) dan TIDAK kena maintenance, hanya security karena zonanya. iot_car_01 rented bergerak juga tidak boleh kena apa-apa. Kalau ada alert dari dua mobil itu, rule-nya salah.

## 4. Part 3: Dispatcher + reconciliation (requirement: backoff+jitter, export JSON)

```bash
podman run --rm -v "$PWD":/app -w /app docker.io/library/python:3.12-slim python3 src/engine/dispatcher.py
```

Harapan:
- 6 alert, masing-masing `attempts=3 delivered=True` (transport simulasi "pulih" di percobaan ke-3, jadi backoff-nya terlihat bekerja).
- File baru: `output/incident_reconciliation.json`.

Spot-check JSON-nya:
```bash
python3 -m json.tool output/incident_reconciliation.json | head -30
```
Harusan berisi `summary` (alerts_raised: 6, delivered: 6, failed: 0), daftar `alerts` dengan tag + reason, dan `delivery` dengan jumlah attempts.

## 5. Idempotensi hasil: jalankan 2x, hasil harus identik

```bash
podman run --rm -v "$PWD":/app -w /app docker.io/library/python:3.12-slim python3 src/engine/dispatcher.py
cp output/incident_reconciliation.json /tmp/run1.json
podman run --rm -v "$PWD":/app -w /app docker.io/library/python:3.12-slim python3 src/engine/dispatcher.py
diff <(python3 -m json.tool /tmp/run1.json | grep -v generated_at) <(python3 -m json.tool output/incident_reconciliation.json | grep -v generated_at) && echo "IDENTIK"
```

## 6. Dokumen
- `docs/NETWORK_ARCHITECTURE.md`: ada diagram Mermaid, tabel VLAN 10/20/30/40/50 lengkap subnet + DHCP + QoS + routing, health check < 3s, throttle guest < 128 kbps saat WAN2.
- `docs/RCA.md`: jelaskan deadlock (circular wait) dan duplicate capture (payment di luar transaksi), dan menunjuk ke fix di `src/patch/`.
- `ai_session_logs/`: STEERING_AUDIT.md + raw log sesi.
