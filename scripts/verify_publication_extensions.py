#!/usr/bin/env python3
"""Independently verify publication-extension tables and reported extrema."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS = PROJECT_ROOT / "results"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, default=RESULTS / "publication_additional_analysis.json")
    parser.add_argument("--tests", type=Path, default=RESULTS / "publication_window_tests.csv")
    parser.add_argument("--controls", type=Path, default=RESULTS / "publication_control_windows.csv")
    parser.add_argument("--lead-lag", type=Path, default=RESULTS / "publication_lead_lag.csv")
    parser.add_argument("--curves", type=Path, default=RESULTS / "publication_lead_lag_curves.csv")
    parser.add_argument("--output", type=Path, default=RESULTS / "publication_additional_verification.json")
    args = parser.parse_args()
    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
    tests = list(csv.DictReader(args.tests.open(encoding="utf-8")))
    controls = list(csv.DictReader(args.controls.open(encoding="utf-8")))
    lead = list(csv.DictReader(args.lead_lag.open(encoding="utf-8")))
    curves = list(csv.DictReader(args.curves.open(encoding="utf-8")))
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: Any) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    check("control_count_360", len(controls) == 360, {"rows": len(controls)})
    check("test_count_36", len(tests) == 36, {"rows": len(tests)})
    check("lead_lag_summary_count_26", len(lead) == 26, {"rows": len(lead)})
    expected_curve_rows = 10 * 11 + 16 * 25
    check("lead_lag_curve_count", len(curves) == expected_curve_rows, {"rows": len(curves), "expected": expected_curve_rows})

    test_differences = []
    for row in tests:
        metric = row["metric"]
        control_set = row["control_set"]
        reported = analysis["window_tests"][metric][control_set]
        for key in (
            "event_value",
            "event_percentile_empirical",
            "permutation_p_exact_plus_one",
            "control_median",
        ):
            csv_value = float(row[key])
            json_value = float(reported[key])
            if not math.isclose(csv_value, json_value, rel_tol=1e-12, abs_tol=1e-12):
                test_differences.append([metric, control_set, key, csv_value, json_value])
        if control_set == "all_360":
            values = [float(control[metric]) for control in controls]
            event = float(row["event_value"])
            percentile = 100 * sum(value <= event for value in values) / len(values)
            median = statistics.median(values)
            direction = row["test_direction"]
            if direction == "upper":
                exceedances = sum(value >= event for value in values)
            elif direction == "lower":
                exceedances = sum(value <= event for value in values)
            else:
                exceedances = sum(abs(value - median) >= abs(event - median) for value in values)
            p_value = (exceedances + 1) / (len(values) + 1)
            if not math.isclose(percentile, float(row["event_percentile_empirical"]), abs_tol=1e-12):
                test_differences.append([metric, control_set, "recomputed_percentile", percentile, row["event_percentile_empirical"]])
            if not math.isclose(p_value, float(row["permutation_p_exact_plus_one"]), abs_tol=1e-12):
                test_differences.append([metric, control_set, "recomputed_p", p_value, row["permutation_p_exact_plus_one"]])
    check("window_test_recalculation", not test_differences, {"differences_first_20": test_differences[:20]})

    curves_by_id: dict[str, list[dict[str, str]]] = {}
    for row in curves:
        curves_by_id.setdefault(row["analysis_id"], []).append(row)
    lead_differences = []
    for row in lead:
        curve = curves_by_id[row["analysis_id"]]
        best = max(curve, key=lambda item: abs(float(item["correlation"])))
        zero = next(item for item in curve if int(item["lag_intervals"]) == 0)
        comparisons = {
            "max_abs_correlation": float(best["correlation"]),
            "max_abs_correlation_lag_minutes": float(best["lag_minutes"]),
            "lag_zero_correlation": float(zero["correlation"]),
        }
        for key, expected in comparisons.items():
            if not math.isclose(float(row[key]), expected, rel_tol=1e-12, abs_tol=1e-12):
                lead_differences.append([row["analysis_id"], key, row[key], expected])
    check("lead_lag_extrema_recalculation", not lead_differences, {"differences_first_20": lead_differences[:20]})

    robust = analysis["robustness"]
    combined = robust["confirmed_only_event_inflow_atom"] + robust["unconfirmed_behavioral_event_inflow_atom"]
    check(
        "robustness_additivity",
        math.isclose(combined, robust["confirmed_plus_unconfirmed_event_inflow_atom"], abs_tol=1e-9),
        {"recomputed_combined": combined, "reported": robust["confirmed_plus_unconfirmed_event_inflow_atom"]},
    )
    limitation = analysis["order_book_limitation"]
    check(
        "order_book_claim_guardrail",
        limitation["historical_binance_order_book_snapshots_in_acquired_data"] is False
        and "consistent" in limitation["permitted_claim"].lower()
        and "prove" in limitation["prohibited_claim"].lower(),
        limitation,
    )
    passed = all(item["passed"] for item in checks)
    output = {
        "study_id": analysis["study_id"],
        "verification_status": "PASS" if passed else "FAIL",
        "checks": checks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "status": output["verification_status"], "checks": len(checks)}, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
