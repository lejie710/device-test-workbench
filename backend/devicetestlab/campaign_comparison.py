"""Read-only comparisons of the latest saved attempts in two campaigns."""

from __future__ import annotations

import copy
import json
from collections import Counter
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from fastapi.responses import Response

from .comparison import SUMMARY_FIELDS, compare_runs

COUNTS = (
    "compared_tests",
    "new_failures",
    "resolved_failures",
    "persistent_failures",
    "unchanged_passes",
)
CAMPAIGN_FIELDS = ("id", "name", "state", "created_at", "finished_at", "total", "completed")


def _summary(campaign: dict) -> dict:
    plan = campaign.get("plan_snapshot") or {}
    return copy.deepcopy(
        {
            **{field: campaign.get(field) for field in CAMPAIGN_FIELDS},
            "plan_name": plan.get("name"),
            "plan_kind": plan.get("kind"),
            "definition_fingerprint": plan.get("sha256"),
            "engine_fingerprint": campaign.get("engine_fingerprint"),
        }
    )


def _index(campaign: dict) -> dict[tuple[str, int], list[dict]]:
    indexed: dict[tuple[str, int], list[dict]] = {}
    for case in campaign["cases"]:
        indexed.setdefault((case["scenario"], case["seed"]), []).append(case)
    return indexed


def _resolve(
    campaign: dict, cases: list[dict], get_run: Callable[[str], dict | None]
) -> tuple[dict, dict | None, str | None]:
    """Use the explicit latest pointer, rejecting broken links without substituting a retry."""
    case = cases[0]
    warnings = []
    invalid = False
    if len(cases) != 1:
        invalid = True
        warnings.append("Duplicate scenario/seed cases make this alignment ambiguous.")
    if sum(other.get("id") == case.get("id") for other in campaign["cases"]) != 1:
        invalid = True
        warnings.append("The campaign contains a duplicate case identifier.")
    attempts = case.get("attempts", [])
    valid_attempts = (
        isinstance(attempts, list)
        and all(isinstance(item, str) and item for item in attempts)
        and len(set(attempts)) == len(attempts)
    )
    if not valid_attempts:
        invalid = True
        warnings.append("The saved attempt list is invalid or contains duplicate run identifiers.")
        attempts = attempts if isinstance(attempts, list) else []
    identifier = case.get("run_id")
    if identifier is not None and (not isinstance(identifier, str) or not identifier):
        invalid = True
        warnings.append("The latest attempt has no valid run identifier.")
    if (attempts and identifier != attempts[-1]) or (identifier and identifier not in attempts):
        invalid = True
        warnings.append(
            "The latest run pointer does not match the last recorded attempt. "
            "Earlier attempts are not substituted."
        )
    run = get_run(identifier) if isinstance(identifier, str) and identifier else None
    if run is not None:
        expected = {
            "id": identifier,
            "campaign_id": campaign["id"],
            "campaign_case_id": case["id"],
            "scenario": case["scenario"],
            "seed": case["seed"],
            "attempt_number": len(attempts),
        }
        for field, value in expected.items():
            if run.get(field) != value or (
                field in {"seed", "attempt_number"} and isinstance(run.get(field), bool)
            ):
                invalid = True
                warnings.append(f"Saved run {field.replace('_', ' ')} does not match its case.")
        plan = campaign.get("plan_snapshot") or {}
        definition = run.get("definition_snapshot") or {}
        for label, before, after in (
            ("definition fingerprint", plan.get("sha256"), definition.get("sha256")),
            ("suite kind", plan.get("kind"), run.get("suite_kind")),
            (
                "engine fingerprint",
                campaign.get("engine_fingerprint"),
                run.get("engine_fingerprint"),
            ),
        ):
            if before != after:
                invalid = True
                warnings.append(f"Saved run {label} does not match its campaign snapshot.")
        if plan.get("kind") == "custom" and run.get("plan_snapshot") != plan:
            invalid = True
            warnings.append("Saved custom plan does not match its campaign snapshot.")
        if plan.get("kind") == "custom" and isinstance(plan.get("plan"), dict):
            plan_steps = {test["id"]: test["steps"] for test in plan["plan"].get("tests", [])}
            if definition.get("steps") != plan_steps:
                invalid = True
                warnings.append("Saved command definitions do not match the custom campaign plan.")
        expected_definition = plan.get("definition_snapshot")
        if expected_definition is not None and definition != expected_definition:
            invalid = True
            warnings.append("Saved test definitions do not match the campaign snapshot.")
        expected_tests = definition.get("tests")
        observed_tests = run.get("tests")
        if isinstance(expected_tests, list) and isinstance(observed_tests, list):
            expected_ids = [test.get("id") for test in expected_tests]
            observed_ids = [test.get("id") for test in observed_tests if isinstance(test, dict)]
            if (
                len(expected_ids) != plan.get("test_count")
                or run.get("total") != len(expected_ids)
                or Counter(expected_ids) != Counter(observed_ids)
            ):
                invalid = True
                warnings.append("Saved test membership does not match the campaign's full plan.")
        else:
            invalid = True
            warnings.append("Saved full-plan test membership is unavailable.")
    elif identifier:
        warnings.append(
            "The latest saved run is missing. Earlier attempts are retained but not substituted."
        )
    else:
        warnings.append("This case has no saved latest attempt.")
    descriptor = {
        "case_id": case.get("id"),
        "state": case.get("state"),
        "run_id": copy.deepcopy(identifier),
        "attempts": copy.deepcopy(attempts),
        "attempt_count": len(attempts),
        "run": {field: copy.deepcopy(run.get(field)) for field in SUMMARY_FIELDS} if run else None,
        "warnings": warnings,
    }
    if invalid:
        return descriptor, run, "invalid_link"
    if identifier and run is None:
        return descriptor, None, "missing_run"
    if case.get("state") != "completed" or run is None or run.get("state") != "completed":
        warnings.append(
            "The latest case and run must both be completed. Pending work, interruptions "
            "and runner errors are not device passes or failures."
        )
        return descriptor, run, "incomplete"
    return descriptor, run, None


def _delta(comparison: dict) -> dict[str, int]:
    transitions = Counter(
        (test["left"]["status"], test["right"]["status"]) for test in comparison["tests"]
    )
    new = transitions["pass", "fail"]
    resolved = transitions["fail", "pass"]
    persistent = transitions["fail", "fail"]
    unchanged = transitions["pass", "pass"]
    return {
        "compared_tests": new + resolved + persistent + unchanged,
        "new_failures": new,
        "resolved_failures": resolved,
        "persistent_failures": persistent,
        "unchanged_passes": unchanged,
        "failed_left": resolved + persistent,
        "failed_right": new + persistent,
        "failed_delta": new - resolved,
    }


def compare_campaigns(
    left: dict, right: dict, get_run: Callable[[str], dict | None]
) -> dict[str, Any]:
    """Align conditions, inspect latest attempts and aggregate only independent comparable pairs.

    get_run must return saved evidence, never launch work. Returned nested data are independent
    copies. Each run is read at most once, including when the same campaign is selected twice.
    """
    left_index, right_index = _index(left), _index(right)
    left_summary, right_summary = _summary(left), _summary(right)
    conditions = [
        {
            "field": field,
            "left": copy.deepcopy(left_summary[field]),
            "right": copy.deepcopy(right_summary[field]),
            "changed": left_summary[field] != right_summary[field],
        }
        for field in ("plan_kind", "plan_name", "definition_fingerprint", "engine_fingerprint")
    ]
    for field, index in (
        ("scenarios", 0),
        ("seeds", 1),
    ):
        before = sorted({key[index] for key in left_index})
        after = sorted({key[index] for key in right_index})
        conditions.append(
            {"field": field, "left": before, "right": after, "changed": before != after}
        )
    warnings = []
    context_warnings = []
    same_campaign = left.get("id") == right.get("id")
    if same_campaign:
        context_warnings.append(
            "Both selections identify the same saved campaign; there are no independent "
            "observations, so no cases enter the comparison counts."
        )
    for field in ("plan_kind", "definition_fingerprint", "engine_fingerprint"):
        before, after = left_summary[field], right_summary[field]
        if not before or not after:
            context_warnings.append(
                f"A saved campaign {field.replace('_', ' ')} is unavailable; "
                "equivalence is unverified."
            )
        elif before != after:
            context_warnings.append(
                f"Different campaign {field.replace('_', ' ')} values change the conditions."
            )
    warnings.extend(context_warnings)
    keys = list(left_index) + [key for key in right_index if key not in left_index]
    cache: dict[str, dict | None] = {}

    def saved(identifier: str) -> dict | None:
        if identifier not in cache:
            cache[identifier] = get_run(identifier)
        return cache[identifier]

    rows: list[dict[str, Any]] = []
    for scenario, seed in keys:
        key = (scenario, seed)
        left_info, left_run, left_issue = (
            _resolve(left, left_index[key], saved) if key in left_index else (None, None, None)
        )
        right_info, right_run, right_issue = (
            _resolve(right, right_index[key], saved) if key in right_index else (None, None, None)
        )
        row_warnings: list[str] = []
        for label, info in (("Left", left_info), ("Right", right_info)):
            if info:
                row_warnings.extend(f"{label}: {message}" for message in info["warnings"])
        comparison = (
            compare_runs(left_run, right_run)
            if left_run is not None and right_run is not None
            else None
        )
        if left_info is None:
            status = "added"
            row_warnings.append("This scenario/seed case exists only in the right campaign.")
        elif right_info is None:
            status = "removed"
            row_warnings.append("This scenario/seed case exists only in the left campaign.")
        else:
            row_warnings.extend(context_warnings)
            if comparison is not None:
                row_warnings.extend(comparison["warnings"])
            status = next(
                (
                    issue
                    for issue in ("invalid_link", "missing_run", "incomplete")
                    if issue in (left_issue, right_issue)
                ),
                "compared",
            )
            if status == "compared" and (
                context_warnings
                or comparison is None
                or not comparison["comparable"]
                or (
                    left_run is not None
                    and right_run is not None
                    and left_run["id"] == right_run["id"]
                )
            ):
                status = "incomparable"
        comparable = status == "compared"
        # The nested drilldown must never contradict the campaign's stronger integrity gate.
        if comparison is not None and not comparable:
            comparison["comparable"] = False
            comparison["warnings"] = list(dict.fromkeys(comparison["warnings"] + row_warnings))
        rows.append(
            {
                "key": f"{scenario}:{seed}",
                "scenario": scenario,
                "seed": seed,
                "status": status,
                "comparable": comparable,
                "warnings": list(dict.fromkeys(row_warnings)),
                "left": left_info,
                "right": right_info,
                "delta": _delta(comparison) if comparable and comparison is not None else None,
                "comparison": comparison,
            }
        )
    summary = {
        "total_cases": len(rows),
        "matched_cases": len(set(left_index) & set(right_index)),
        "comparable_cases": sum(row["comparable"] for row in rows),
        "not_compared_cases": sum(not row["comparable"] for row in rows),
        "added_cases": sum(row["status"] == "added" for row in rows),
        "removed_cases": sum(row["status"] == "removed" for row in rows),
        "new_failure_cases": 0,
        "resolved_failure_cases": 0,
        "changed_cases": 0,
        "unchanged_cases": 0,
        **dict.fromkeys(COUNTS, 0),
    }
    for row in rows:
        delta = row["delta"]
        if delta is None:
            continue
        for field in COUNTS:
            summary[field] += delta[field]
        new, resolved = delta["new_failures"] > 0, delta["resolved_failures"] > 0
        summary["new_failure_cases"] += new
        summary["resolved_failure_cases"] += resolved
        summary["changed_cases"] += new or resolved
        summary["unchanged_cases"] += not (new or resolved)
    if summary["added_cases"] or summary["removed_cases"]:
        warnings.append(
            "Case membership differs. Added and removed cases stay outside the paired denominator."
        )
    if summary["not_compared_cases"]:
        warnings.append(
            f"{summary['not_compared_cases']} of {summary['total_cases']} aligned cases are not "
            "compared. Inspect each case for changed conditions, incomplete work "
            "or missing evidence."
        )
    if not summary["comparable_cases"]:
        warnings.append(
            "No independent comparable cases are available; zero counts do not mean a pass."
        )
    return {
        "schema_version": "1.0",
        "left": left_summary,
        "right": right_summary,
        "conditions": conditions,
        "warnings": warnings,
        "comparable": bool(rows) and summary["not_compared_cases"] == 0,
        "summary": summary,
        "cases": rows,
        "note": (
            "Cases align by scenario and seed. Only the saved latest attempt is used. "
            "Counts include only independent, completed, comparable PASS/FAIL pairs; missing "
            "work and runner errors are excluded. New failures mean observed PASS to FAIL "
            "transitions, not a causal regression verdict. Deltas are right minus left. "
            "This report is a saved-evidence view; it does not execute tests or modify campaigns."
        ),
    }


def register_campaign_comparison_routes(application, storage) -> None:
    def report(left: str, right: str) -> dict:
        before, after = storage.get_campaign(left), storage.get_campaign(right)
        if before is None or after is None:
            raise HTTPException(404, "Campaign not found.")
        return compare_campaigns(before, after, storage.get_run)

    @application.get("/api/campaign-compare")
    def compare(left: str, right: str) -> dict:
        return report(left, right)

    @application.get("/api/campaign-compare/export")
    def export(left: str, right: str) -> Response:
        payload = report(left, right)
        return Response(
            json.dumps(payload, indent=2, allow_nan=False),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="campaign-comparison.json"'},
        )
