"""Saved-run comparison retains evidence and makes changed conditions explicit."""

import copy

from devicetestlab.comparison import compare_runs
from devicetestlab.engine import run_suite
from devicetestlab.engine import test_definition_snapshot as definition_snapshot


def saved_run(identifier, scenario="healthy", seed=3):
    results = run_suite(scenario, seed)
    return {
        "id": identifier,
        "state": "completed",
        "scenario": scenario,
        "seed": seed,
        "strategy": "default",
        "tests": results,
        "test_order": [test["id"] for test in results],
        "total": len(results),
        "passed": sum(test["status"] == "pass" for test in results),
        "failed": sum(test["status"] == "fail" for test in results),
        "errors": 0,
        "duration_ms": 10.0,
        "first_failure_ms": None,
        "definition_snapshot": definition_snapshot(),
        "engine_fingerprint": "same-saved-engine",
    }


def test_same_conditions_and_legacy_v1_snapshots_are_comparable():
    left, right = saved_run("one"), saved_run("two")
    right["strategy"] = "recent_first"
    right["test_order"] = list(reversed(right["test_order"]))
    right["duration_ms"] = 12
    report = compare_runs(left, right)
    assert report["comparable"]
    assert report["summary"]["unchanged_verdicts"] == 14
    assert report["timing"]["wall_time_ms"]["delta"] == 2
    assert report["timing"]["modeled_cost_ms"]["delta"] == 0
    assert report["timing"]["first_failure_wall_time_ms"]["delta"] is None
    assert any("ordering differs" in message for message in report["warnings"])


def test_changed_scenario_is_observation_not_an_automatic_regression():
    left, right = saved_run("one"), saved_run("two", "ignored_write")
    report = compare_runs(left, right)
    assert not report["comparable"]
    assert report["summary"]["changed_verdicts"] == 5
    single = next(test for test in report["tests"] if test["id"] == "single_byte")
    assert single["left"]["status"] == "pass" and single["right"]["status"] == "fail"
    assert single["right"]["expected"]["value"] == 90
    assert single["right"]["actual"]["value"] == 0
    assert single["right"]["trace"][0]["response"] == {"ok": True}
    assert "No causal or regression verdict" in report["timing"]["note"]


def test_added_removed_and_changed_definitions_are_not_hidden():
    left, right = saved_run("one"), saved_run("two")
    removed = right["tests"].pop(0)
    extra = copy.deepcopy(removed)
    extra["id"] = "custom_status"
    right["tests"].append(extra)
    right["definition_snapshot"]["sha256"] = "different-definition"
    right["definition_snapshot"]["steps"]["single_byte"][1]["expected"]["value"] = 3
    report = compare_runs(left, right)
    assert not report["comparable"]
    assert report["added_test_ids"] == ["custom_status"]
    assert report["removed_test_ids"] == ["initial_status"]
    assert next(test for test in report["tests"] if test["id"] == "single_byte")[
        "definition_changed"
    ]


def test_pending_and_runner_error_are_preserved_and_not_counted_as_device_failure():
    left, right = saved_run("one"), saved_run("two")
    right["state"] = "interrupted"
    right["tests"][0]["status"] = "error"
    right["tests"][1]["status"] = "pending"
    right["tests"][1]["modeled_cost_ms"] = 99999
    report = compare_runs(left, right)
    assert not report["comparable"]
    statuses = {entry["right_status"] for entry in report["changed_verdicts"]}
    assert statuses == {"error", "pending"}
    assert report["timing"]["modeled_cost_ms"]["right"] == 834
    assert any("not a complete suite" in warning for warning in report["warnings"])


def test_comparison_returns_independent_evidence_and_flags_missing_fingerprint():
    left, right = saved_run("one"), saved_run("two")
    del right["definition_snapshot"]
    report = compare_runs(left, right)
    assert not report["comparable"]
    report["tests"][0]["left"]["trace"][0]["expected"]["state"] = "changed"
    assert left["tests"][0]["trace"][0]["expected"]["state"] == "ready"


def test_duplicate_ids_cannot_be_presented_as_a_complete_comparable_suite():
    left, right = saved_run("one"), saved_run("two")
    right["tests"][-1] = copy.deepcopy(right["tests"][0])
    report = compare_runs(left, right)
    assert not report["comparable"]
    assert any("Duplicate test" in warning for warning in report["warnings"])


def test_changed_or_missing_engine_context_is_disclosed():
    left, right = saved_run("one"), saved_run("two")
    right["engine_fingerprint"] = "changed-engine"
    report = compare_runs(left, right)
    assert not report["comparable"]
    assert any("engine fingerprint" in warning for warning in report["warnings"])
    del right["engine_fingerprint"]
    report = compare_runs(left, right)
    assert not report["comparable"]
    assert any("cannot be verified" in warning for warning in report["warnings"])
