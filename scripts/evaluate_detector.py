"""
CloudGuardian AI - Anomaly Detector Evaluation Harness (Phase 3)
-------------------------------------------------------------------
Runs a series of real chaos-injection tests against the live stack and
measures how well the anomaly detector actually performs, instead of
just eyeballing a graph.

How it works:
  1. For each (service, chaos_type) pair, trigger a real synthetic
     failure via the service's /chaos/{type} endpoint and record the
     exact time window during which the system SHOULD be flagged as
     anomalous (the ground truth label).
  2. Wait for the chaos window to run its course, then a short cooldown
     so metrics return to baseline before the next test.
  3. Once all tests are done, pull every detection the anomaly-detector
     logged during the whole run.
  4. Slice the whole test period into 15-second buckets (matching the
     poll interval), label each bucket as "should have been flagged"
     or not, and compare against what was actually detected.
  5. Report precision, recall, and F1 per chaos type and overall.

Run this from your host machine (not inside Docker) once the full
stack is up via `docker compose up`, since it talks to the services
over their published localhost ports.

Usage:
    pip install requests
    python scripts/evaluate_detector.py
"""

import time
from datetime import datetime, timedelta, timezone

import requests

SERVICE_PORTS = {
    "auth-service": 8001,
    "payment-service": 8002,
    "inventory-service": 8003,
}
ANOMALY_DETECTOR_URL = "http://localhost:8020"

CHAOS_TYPES = ["cpu_spike", "memory_leak", "latency_spike", "error_storm"]
CHAOS_DURATION_SECONDS = 60
DETECTION_GRACE_SECONDS = 20  # allow the detector a bit of time to react after chaos starts/ends
COOLDOWN_SECONDS = 30         # let metrics settle back to baseline between tests
BUCKET_SECONDS = 15           # matches the metrics-collector poll interval


def trigger_chaos(service: str, chaos_type: str, duration: int) -> bool:
    port = SERVICE_PORTS[service]
    resp = requests.post(
        f"http://localhost:{port}/chaos/{chaos_type}",
        params={"duration_seconds": duration},
        timeout=10,
    )
    return resp.status_code == 200


def stop_chaos(service: str) -> None:
    port = SERVICE_PORTS[service]
    requests.post(f"http://localhost:{port}/chaos/stop", timeout=10)


def fetch_detections(service: str, minutes: int) -> list:
    resp = requests.get(
        f"{ANOMALY_DETECTOR_URL}/anomalies/history",
        params={"service": service, "minutes": minutes},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["anomalies"]


def bucketize_and_score(label_windows: list, detections: list, test_start: datetime, test_end: datetime):
    """
    label_windows: list of (start, end) datetimes where an anomaly SHOULD be flagged
    detections: list of dicts with 'detected_at' ISO timestamps
    Returns (tp, fp, fn, tn) bucket counts.
    """
    detection_times = [
        datetime.fromisoformat(d["detected_at"].replace("Z", "+00:00")) for d in detections
    ]

    tp = fp = fn = tn = 0
    t = test_start
    while t < test_end:
        bucket_end = t + timedelta(seconds=BUCKET_SECONDS)
        is_positive = any(start <= t < end for start, end in label_windows)
        was_detected = any(t <= dt < bucket_end for dt in detection_times)

        if is_positive and was_detected:
            tp += 1
        elif is_positive and not was_detected:
            fn += 1
        elif not is_positive and was_detected:
            fp += 1
        else:
            tn += 1

        t = bucket_end

    return tp, fp, fn, tn


def precision_recall_f1(tp, fp, fn):
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return precision, recall, f1


def run_test(service: str, chaos_type: str):
    print(f"\n--- Testing {service} / {chaos_type} ---")
    window_start = datetime.now(timezone.utc)

    ok = trigger_chaos(service, chaos_type, CHAOS_DURATION_SECONDS)
    if not ok:
        print(f"  FAILED to trigger chaos on {service}")
        return None

    print(f"  Injected {chaos_type} for {CHAOS_DURATION_SECONDS}s, waiting...")
    time.sleep(CHAOS_DURATION_SECONDS + DETECTION_GRACE_SECONDS)

    window_end = datetime.now(timezone.utc)
    stop_chaos(service)

    print(f"  Cooling down for {COOLDOWN_SECONDS}s before next test...")
    time.sleep(COOLDOWN_SECONDS)

    return service, chaos_type, window_start, window_end


def main():
    print("=" * 60)
    print("CloudGuardian AI - Anomaly Detector Evaluation")
    print("=" * 60)

    overall_start = datetime.now(timezone.utc)
    results_by_service = {}  # service -> list of (chaos_type, start, end)

    for service in SERVICE_PORTS:
        results_by_service[service] = []
        for chaos_type in CHAOS_TYPES:
            result = run_test(service, chaos_type)
            if result:
                _, ctype, start, end = result
                results_by_service[service].append((ctype, start, end))

    overall_end = datetime.now(timezone.utc)
    total_minutes = int((overall_end - overall_start).total_seconds() / 60) + 2

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)

    grand_tp = grand_fp = grand_fn = grand_tn = 0

    for service, tests in results_by_service.items():
        print(f"\n{service}:")
        detections = fetch_detections(service, total_minutes)

        for chaos_type, start, end in tests:
            label_windows = [(start, end)]
            tp, fp, fn, tn = bucketize_and_score(label_windows, detections, start, end)
            precision, recall, f1 = precision_recall_f1(tp, fp, fn)
            print(
                f"  {chaos_type:15s} | TP={tp:3d} FP={fp:3d} FN={fn:3d} TN={tn:3d} "
                f"| precision={precision:.2f} recall={recall:.2f} f1={f1:.2f}"
            )
            grand_tp += tp
            grand_fp += fp
            grand_fn += fn
            grand_tn += tn

    print("\n" + "-" * 60)
    precision, recall, f1 = precision_recall_f1(grand_tp, grand_fp, grand_fn)
    print(
        f"OVERALL | TP={grand_tp} FP={grand_fp} FN={grand_fn} TN={grand_tn} "
        f"| precision={precision:.3f} recall={recall:.3f} f1={f1:.3f}"
    )
    print("=" * 60)
    print(
        "\nNote: buckets are labeled positive only during the exact chaos "
        "window plus detection grace period. Some 'false positives' near "
        "window edges are expected since real anomalies don't start/stop "
        "instantly. Use this as a directional signal, and inspect the raw "
        "timestamps if a number looks off."
    )


if __name__ == "__main__":
    main()
