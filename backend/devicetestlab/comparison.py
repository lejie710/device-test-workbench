"""Compare saved observations without assigning cause or a universal regression threshold."""

from __future__ import annotations

import copy
import math
from typing import Any

RESULT_STATES = {"pass", "fail", "error", "pending", "running"}
SUMMARY_FIELDS = (
    "id",
    "created_at",
    "finished_at",
    "state",
    "scenario",
    "strategy",
    "seed",
    "passed",
    "failed",
    "errors",
    "total",
    "duration_ms",
    "first_failure_ms",
    "model_status",
    "suite_kind",
    "plan_name",
    "campaign_id",
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _delta(left: Any, right: Any) -> dict[str, float | None]:
    left_value, right_value = _number(left), _number(right)
    return {
        "left": left_value,
        "right": right_value,
        "delta": right_value - left_value
        if left_value is not None and right_value is not None
        else None,
    }


def _snapshot(run: dict) -> dict:
    snapshot = run.get("definition_snapshot")
    return snapshot if isinstance(snapshot, dict) else {}


def _kind(run: dict) -> str:
    # V1 standard runs had no suite_kind field, but did preserve their definitions.
    return run.get("suite_kind") or ("custom" if run.get("plan_snapshot") else "standard")


def _index(run: dict) -> tuple[dict[str, dict], list[str]]:
    indexed: dict[str, dict] = {}
    errors = []
    tests = run.get("tests")
    if not isinstance(tests, list):
        return indexed, ["Test observations are missing."]
    for result in tests:
        if not isinstance(result, dict) or not isinstance(result.get("id"), str):
            errors.append("A test observation has no valid identifier.")
            continue
        if result["id"] in indexed:
            errors.append(f"Duplicate test identifier: {result['id']}.")
        else:
            indexed[result["id"]] = result
        if result.get("status") not in RESULT_STATES:
            errors.append(f"Unknown observation status for {result['id']}.")
    return indexed, errors


def _complete(run: dict, indexed: dict[str, dict]) -> bool:
    total = run.get("total")
    return (
        run.get("state") == "completed"
        and isinstance(total, int)
        and not isinstance(total, bool)
        and total > 0
        and len(indexed) == total
        and all(result.get("status") in {"pass", "fail"} for result in indexed.values())
    )


def _modeled_total(indexed: dict[str, dict]) -> float | None:
    observed = [
        result for result in indexed.values() if result.get("status") in {"pass", "fail", "error"}
    ]
    values = [_number(result.get("modeled_cost_ms")) for result in observed]
    if not values or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def compare_runs(left: dict, right: dict) -> dict:
    """Return an independent comparison; never modify either archived execution."""
    left_index, left_errors = _index(left)
    right_index, right_errors = _index(right)
    left_snapshot, right_snapshot = _snapshot(left), _snapshot(right)
    conditions = []
    pairs = {
        "scenario": (left.get("scenario"), right.get("scenario")),
        "seed": (left.get("seed"), right.get("seed")),
        "strategy": (left.get("strategy"), right.get("strategy")),
        "suite_kind": (_kind(left), _kind(right)),
        "plan_name": (left.get("plan_name"), right.get("plan_name")),
        "suite_version": (left_snapshot.get("suite_version"), right_snapshot.get("suite_version")),
        "definition_fingerprint": (left_snapshot.get("sha256"), right_snapshot.get("sha256")),
        "engine_fingerprint": (left.get("engine_fingerprint"), right.get("engine_fingerprint")),
        "state": (left.get("state"), right.get("state")),
        "model_status": (left.get("model_status"), right.get("model_status")),
        "test_order": (left.get("test_order"), right.get("test_order")),
    }
    for field, (before, after) in pairs.items():
        conditions.append(
            {
                "field": field,
                "left": copy.deepcopy(before),
                "right": copy.deepcopy(after),
                "changed": before != after,
            }
        )
    warnings = [f"Left run: {message}" for message in left_errors]
    warnings += [f"Right run: {message}" for message in right_errors]
    comparable = not (left_errors or right_errors)
    if left.get("id") == right.get("id"):
        warnings.append(
            "Both selections identify the same saved run; there are no independent observations."
        )
    for side, run, indexed in (("Left", left, left_index), ("Right", right, right_index)):
        if not _complete(run, indexed):
            comparable = False
            warnings.append(
                f"{side} run is not a complete suite of PASS/FAIL observations. "
                "Pending work and runner errors are not device passes or failures."
            )
    for field in (
        "scenario",
        "seed",
        "suite_kind",
        "suite_version",
        "definition_fingerprint",
        "engine_fingerprint",
    ):
        before, after = pairs[field]
        if before != after:
            comparable = False
            warnings.append(
                f"Different {field.replace('_', ' ')} values change the comparison conditions."
            )
    if not left_snapshot.get("sha256") or not right_snapshot.get("sha256"):
        comparable = False
        warnings.append(
            "A saved test-definition fingerprint is unavailable; equivalence is unverified."
        )
    if not left.get("engine_fingerprint") or not right.get("engine_fingerprint"):
        comparable = False
        warnings.append(
            "An engine fingerprint is unavailable. The saved observations are retained, "
            "but implementation equivalence cannot be verified."
        )
    if (
        pairs["strategy"][0] != pairs["strategy"][1]
        or pairs["test_order"][0] != pairs["test_order"][1]
    ):
        warnings.append(
            "Test ordering differs. Compare preserved assertions and modeled work separately "
            "from the time at which the runner reported a failure."
        )
    added = [test_id for test_id in right_index if test_id not in left_index]
    removed = [test_id for test_id in left_index if test_id not in right_index]
    if added or removed:
        comparable = False
        warnings.append(
            "Test membership changed; full-suite totals describe different sets of tests."
        )
    tests, changed = [], []
    common = set(left_index) & set(right_index)
    left_steps, right_steps = left_snapshot.get("steps", {}), right_snapshot.get("steps", {})
    for test_id in list(left_index) + added:
        before, after = left_index.get(test_id), right_index.get(test_id)
        name = (after or before or {}).get("name", test_id)
        verdict_changed = (
            before is not None
            and after is not None
            and (before.get("status") != after.get("status"))
        )
        definition_changed = None
        if test_id in left_steps and test_id in right_steps:
            definition_changed = left_steps[test_id] != right_steps[test_id]
        if verdict_changed and before is not None and after is not None:
            changed.append(
                {
                    "id": test_id,
                    "name": name,
                    "left_status": before.get("status"),
                    "right_status": after.get("status"),
                }
            )
        tests.append(
            {
                "id": test_id,
                "name": name,
                "left": copy.deepcopy(before),
                "right": copy.deepcopy(after),
                "verdict_changed": verdict_changed,
                "definition_changed": definition_changed,
            }
        )
    if any(test["definition_changed"] is True for test in tests):
        comparable = False
        warnings.append(
            "Recorded commands or expected responses differ for shared test identifiers."
        )
    result = {
        "left": {key: copy.deepcopy(left.get(key)) for key in SUMMARY_FIELDS},
        "right": {key: copy.deepcopy(right.get(key)) for key in SUMMARY_FIELDS},
        "comparable": comparable,
        "warnings": warnings,
        "conditions": conditions,
        "summary": {
            "common_tests": len(common),
            "changed_verdicts": len(changed),
            "unchanged_verdicts": len(common) - len(changed),
            "added_tests": len(added),
            "removed_tests": len(removed),
        },
        "added_test_ids": added,
        "removed_test_ids": removed,
        "changed_verdicts": changed,
        "tests": tests,
        "timing": {
            "wall_time_ms": _delta(left.get("duration_ms"), right.get("duration_ms")),
            "modeled_cost_ms": _delta(_modeled_total(left_index), _modeled_total(right_index)),
            "first_failure_wall_time_ms": _delta(
                left.get("first_failure_ms"), right.get("first_failure_ms")
            ),
            "note": (
                "Deltas are right minus left. Laptop wall time includes runner and storage "
                "overhead and is contextual, not physical-device timing. Modeled cost sums "
                "observed test costs; unfinished runs provide only partial work. Null means "
                "unavailable or no observed failure. No causal or regression verdict is inferred."
            ),
        },
    }
    return result
