"""Synthetic saved evidence verifies alignment, denominators and immutable comparison."""

import copy
import importlib
import json
import sqlite3
import time
from collections import Counter

import pytest
from devicetestlab.campaign_comparison import compare_campaigns
from devicetestlab.plans import TestPlan as Plan
from devicetestlab.plans import custom_definition, plan_snapshot, run_plan
from fastapi.testclient import TestClient


def fixture(identifier, conditions=(("healthy", 3),), expected_state="ready"):
    """Build original synthetic records; overrides below model archived edge cases only."""
    plan = Plan.model_validate(
        {
            "schema_version": "1.0",
            "name": "Synthetic status checks",
            "tests": [
                {
                    "id": f"status_{number}",
                    "name": f"Status check {number}",
                    "steps": [
                        {
                            "command": {"op": "STATUS"},
                            "expected": {"ok": True, "state": expected_state, "capacity": 16},
                        }
                    ],
                }
                for number in range(4)
            ],
        }
    )
    snapshot = plan_snapshot(plan)
    runs, cases = {}, []
    for index, (scenario, seed) in enumerate(conditions):
        case_id, run_id = f"{identifier}-case-{index}", f"{identifier}-run-{index}"
        tests = run_plan(snapshot, scenario, seed)
        run = {
            "id": run_id,
            "created_at": "2026-10-13T00:00:00Z",
            "finished_at": "2026-10-13T00:00:01Z",
            "state": "completed",
            "scenario": scenario,
            "seed": seed,
            "strategy": "default",
            "campaign_id": identifier,
            "campaign_case_id": case_id,
            "attempt_number": 1,
            "suite_kind": "custom",
            "plan_name": plan.name,
            "plan_snapshot": copy.deepcopy(snapshot),
            "definition_snapshot": custom_definition(copy.deepcopy(snapshot)),
            "engine_fingerprint": "synthetic-saved-engine",
            "test_order": [test["id"] for test in tests],
            "tests": tests,
            "total": 4,
            "passed": sum(test["status"] == "pass" for test in tests),
            "failed": sum(test["status"] == "fail" for test in tests),
            "errors": 0,
            "duration_ms": 1.0,
            "first_failure_ms": None,
            "events": [],
            "error": None,
        }
        runs[run_id] = run
        cases.append(
            {
                "id": case_id,
                "scenario": scenario,
                "seed": seed,
                "state": "completed",
                "run_id": run_id,
                "attempts": [run_id],
            }
        )
    return {
        "id": identifier,
        "name": f"Synthetic campaign {identifier}",
        "state": "completed",
        "created_at": "2026-10-13T00:00:00Z",
        "finished_at": "2026-10-13T00:00:01Z",
        "plan_snapshot": snapshot,
        "engine_fingerprint": "synthetic-saved-engine",
        "cases": cases,
        "completed": len(cases),
        "total": len(cases),
        "error": None,
        "pause_requested": False,
    }, runs


def compare(left_fixture, right_fixture):
    left, left_runs = left_fixture
    right, right_runs = right_fixture
    return compare_campaigns(left, right, {**left_runs, **right_runs}.get)


def set_statuses(run, statuses):
    for test, status in zip(run["tests"], statuses, strict=True):
        test["status"] = status
        if status == "fail":
            test["actual"] = {"ok": True, "state": "busy", "capacity": 16}
            test["message"] = "Synthetic saved mismatch for comparison coverage."
    counts = Counter(statuses)
    run.update(passed=counts["pass"], failed=counts["fail"], errors=counts["error"])


def test_reordered_cases_align_by_conditions_not_position():
    left = fixture("left", (("healthy", 3), ("ignored_write", 7), ("healthy", 8)))
    right = fixture("right", (("healthy", 8), ("healthy", 3), ("ignored_write", 7)))
    report = compare(left, right)
    assert report["comparable"]
    assert report["summary"]["comparable_cases"] == 3
    assert report["summary"]["compared_tests"] == 12
    assert report["summary"]["unchanged_passes"] == 12
    first = report["cases"][0]
    assert first["key"] == "healthy:3"
    assert first["left"]["run_id"] == "left-run-0"
    assert first["right"]["run_id"] == "right-run-1"


def test_added_removed_cases_stay_outside_paired_denominator():
    report = compare(
        fixture("left", (("healthy", 1), ("healthy", 3))),
        fixture("right", (("healthy", 3), ("healthy", 7))),
    )
    assert report["summary"] == {
        "total_cases": 3,
        "matched_cases": 1,
        "comparable_cases": 1,
        "not_compared_cases": 2,
        "added_cases": 1,
        "removed_cases": 1,
        "new_failure_cases": 0,
        "resolved_failure_cases": 0,
        "changed_cases": 0,
        "unchanged_cases": 1,
        "compared_tests": 4,
        "new_failures": 0,
        "resolved_failures": 0,
        "persistent_failures": 0,
        "unchanged_passes": 4,
    }
    assert [case["status"] for case in report["cases"]] == ["removed", "compared", "added"]
    assert not report["comparable"]


def test_test_transition_counts_have_precise_denominator_and_case_overlap():
    left, right = fixture("left"), fixture("right")
    set_statuses(left[1]["left-run-0"], ["pass", "fail", "fail", "pass"])
    set_statuses(right[1]["right-run-0"], ["fail", "pass", "fail", "pass"])
    report = compare(left, right)
    counts = report["summary"]
    assert counts["compared_tests"] == 4
    assert counts["new_failures"] == counts["resolved_failures"] == 1
    assert counts["persistent_failures"] == counts["unchanged_passes"] == 1
    assert counts["new_failure_cases"] == counts["resolved_failure_cases"] == 1
    assert counts["changed_cases"] == 1 and counts["unchanged_cases"] == 0
    assert report["cases"][0]["delta"]["failed_delta"] == 0
    assert report["cases"][0]["comparison"]["tests"][0]["right"]["actual"]["state"] == "busy"


def test_changed_definition_retains_observations_but_never_calls_them_new_failures():
    report = compare(fixture("left"), fixture("right", expected_state="busy"))
    row = report["cases"][0]
    assert row["status"] == "incomparable" and row["delta"] is None
    assert report["summary"]["not_compared_cases"] == 1
    assert report["summary"]["compared_tests"] == report["summary"]["new_failures"] == 0
    assert len(row["comparison"]["changed_verdicts"]) == 4
    assert all(test["definition_changed"] for test in row["comparison"]["tests"])
    assert any("definition fingerprint" in warning for warning in report["warnings"])


@pytest.mark.parametrize("state", ["pending", "running", "error", "interrupted"])
def test_latest_incomplete_attempt_never_falls_back_to_completed_old_retry(state):
    left, right = fixture("left"), fixture("right")
    campaign, runs = right
    old_run = runs["right-run-0"]
    latest = copy.deepcopy(old_run)
    latest.update(id="latest", state="queued" if state == "pending" else state, attempt_number=2)
    set_statuses(latest, ["error" if state == "error" else "pending"] * 4)
    runs["latest"] = latest
    campaign.update(state="paused", completed=0)
    campaign["cases"][0].update(state=state, run_id="latest", attempts=["right-run-0", "latest"])
    report = compare(left, right)
    row = report["cases"][0]
    assert row["status"] == "incomplete"
    assert row["right"]["run_id"] == "latest" and row["right"]["attempt_count"] == 2
    assert row["right"]["run"]["state"] == latest["state"]
    assert report["summary"]["compared_tests"] == 0 and old_run["state"] == "completed"


def test_latest_completed_retry_is_used_even_when_old_attempt_failed():
    left, right = fixture("left"), fixture("right")
    campaign, runs = right
    latest = runs["right-run-0"]
    old = copy.deepcopy(latest)
    old.update(id="old-failed-attempt", state="interrupted")
    set_statuses(old, ["fail", "pending", "pending", "pending"])
    latest["attempt_number"] = 2
    runs[old["id"]] = old
    campaign["cases"][0]["attempts"].insert(0, old["id"])
    calls = []

    def lookup(identifier):
        calls.append(identifier)
        return {**left[1], **runs}.get(identifier)

    report = compare_campaigns(left[0], campaign, lookup)
    assert report["comparable"] and report["summary"]["new_failures"] == 0
    assert "old-failed-attempt" not in calls
    assert report["cases"][0]["right"]["attempt_count"] == 2


@pytest.mark.parametrize("pointer", ["missing-latest", None, "right-run-0"])
def test_missing_or_stale_latest_does_not_substitute_earlier_attempt(pointer):
    left, right = fixture("left"), fixture("right")
    case = right[0]["cases"][0]
    case.update(run_id=pointer, attempts=["right-run-0", "missing-latest"])
    report = compare(left, right)
    row = report["cases"][0]
    assert row["status"] == ("missing_run" if pointer == "missing-latest" else "invalid_link")
    assert row["right"]["run_id"] == pointer
    assert row["delta"] is None and report["summary"]["compared_tests"] == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("campaign_id", "another-campaign"),
        ("campaign_case_id", "another-case"),
        ("scenario", "timeout"),
        ("seed", 99),
        ("attempt_number", 99),
        ("id", "another-run"),
        ("engine_fingerprint", "another-engine"),
    ],
)
def test_broken_case_run_links_are_visible_and_excluded(field, value):
    left, right = fixture("left"), fixture("right")
    right[1]["right-run-0"][field] = value
    report = compare(left, right)
    assert report["cases"][0]["status"] == "invalid_link"
    assert report["summary"]["comparable_cases"] == 0
    assert not report["cases"][0]["comparison"]["comparable"]


def test_same_campaign_is_not_independent_and_each_latest_run_is_read_once():
    campaign, runs = fixture("same")
    calls = []

    def lookup(identifier):
        calls.append(identifier)
        return runs.get(identifier)

    report = compare_campaigns(campaign, campaign, lookup)
    assert not report["comparable"]
    assert report["summary"]["compared_tests"] == 0
    assert report["summary"]["not_compared_cases"] == 1
    assert any("no independent observations" in warning for warning in report["warnings"])
    assert calls == ["same-run-0"]


def test_pending_case_without_attempt_is_visible_without_fabricated_metadata():
    left, right = fixture("left"), fixture("right")
    right[0]["cases"][0].update(state="pending", run_id=None, attempts=[])
    report = compare(left, right)
    row = report["cases"][0]
    assert row["status"] == "incomplete" and row["comparison"] is None
    assert row["right"]["run"] is None and row["right"]["run_id"] is None


def test_missing_engine_provenance_is_not_filled_from_current_engine():
    left, right = fixture("left"), fixture("right")
    del right[0]["engine_fingerprint"]
    del right[1]["right-run-0"]["engine_fingerprint"]
    report = compare(left, right)
    assert report["cases"][0]["status"] == "incomparable"
    assert report["right"]["engine_fingerprint"] is None
    assert report["summary"]["compared_tests"] == 0


def test_duplicate_case_conditions_are_not_silently_compared():
    left, right = fixture("left"), fixture("right", (("healthy", 3), ("healthy", 3)))
    report = compare(left, right)
    assert len(report["cases"]) == 1
    assert report["cases"][0]["status"] == "invalid_link"
    assert any("ambiguous" in warning for warning in report["cases"][0]["warnings"])


def test_truncated_equal_observations_cannot_masquerade_as_the_full_plan():
    left, right = fixture("left"), fixture("right")
    for _, runs in (left, right):
        for run in runs.values():
            run["tests"].pop()
            run["total"] = 3
    report = compare(left, right)
    assert report["cases"][0]["status"] == "invalid_link"
    assert report["summary"]["compared_tests"] == 0


def test_matching_run_definitions_must_still_agree_with_saved_custom_plan():
    left, right = fixture("left"), fixture("right")
    for _, runs in (left, right):
        for run in runs.values():
            run["definition_snapshot"]["steps"]["status_0"][0]["expected"]["state"] = "busy"
    report = compare(left, right)
    assert report["cases"][0]["status"] == "invalid_link"
    assert report["summary"]["compared_tests"] == 0


def test_missing_saved_definition_membership_cannot_be_inferred_from_results():
    left, right = fixture("left"), fixture("right")
    for _, runs in (left, right):
        for run in runs.values():
            del run["definition_snapshot"]["tests"]
    report = compare(left, right)
    assert report["cases"][0]["status"] == "invalid_link"
    assert report["summary"]["compared_tests"] == 0


def test_returned_report_and_inputs_do_not_share_mutable_evidence():
    left, right = fixture("left"), fixture("right")
    original = copy.deepcopy((left, right))
    report = compare(left, right)
    assert (left, right) == original
    row = report["cases"][0]
    row["left"]["attempts"].append("changed")
    row["comparison"]["tests"][0]["left"]["trace"][0]["expected"]["state"] = "changed"
    assert (left, right) == original


@pytest.fixture
def application(tmp_path, monkeypatch):
    monkeypatch.setenv("DTL_RUNTIME_DIR", str(tmp_path / "module"))
    return importlib.import_module("devicetestlab.app").create_app(
        tmp_path / "runtime", tmp_path / "dist"
    )


def snapshot_bytes(storage):
    with sqlite3.connect(storage.path) as connection:
        return {
            table: connection.execute(f"SELECT id, snapshot FROM {table} ORDER BY id").fetchall()
            for table in ("campaigns", "runs", "experiment")
        }


def test_api_and_export_are_read_only_even_while_worker_busy(application):
    storage = application.state.storage
    with TestClient(application) as client:
        for campaign, runs in (fixture("left"), fixture("right")):
            storage.save_campaign(campaign)
            for run in runs.values():
                storage.save_run(run)
        storage.save_experiment({"state": "not_trained", "synthetic": True})
        before = snapshot_bytes(storage)
        assert application.state.worker_gate.acquire(blocking=False)
        try:
            response = client.get(
                "/api/campaign-compare", params={"left": "left", "right": "right"}
            )
            export = client.get(
                "/api/campaign-compare/export", params={"left": "left", "right": "right"}
            )
            assert application.state.worker_gate.locked()
        finally:
            application.state.worker_gate.release()
        assert response.status_code == export.status_code == 200
        assert export.json() == response.json()
        assert response.json()["summary"]["compared_tests"] == 4
        assert "attachment" in export.headers["content-disposition"]
        assert json.loads(export.content) == response.json()
        assert snapshot_bytes(storage) == before


@pytest.mark.parametrize("endpoint", ["/api/campaign-compare", "/api/campaign-compare/export"])
def test_api_rejects_absent_campaign_and_missing_query_without_writes(application, endpoint):
    with TestClient(application) as client:
        before = snapshot_bytes(application.state.storage)
        response = client.get(endpoint, params={"left": "absent", "right": "also-absent"})
        assert response.status_code == 404 and response.json()["detail"] == "Campaign not found."
        assert client.get(endpoint, params={"left": "absent"}).status_code == 422
        assert snapshot_bytes(application.state.storage) == before


@pytest.mark.parametrize("custom", [False, True])
def test_actual_campaigns_compare_after_execution_with_reordered_conditions(application, custom):
    plan = fixture("source")[0]["plan_snapshot"]["plan"] if custom else None

    def create_and_wait(client, name, scenarios, seeds):
        response = client.post(
            "/api/campaigns",
            json={"name": name, "scenarios": scenarios, "seeds": seeds, "plan": plan},
        )
        assert response.status_code == 202
        identifier = response.json()["id"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            campaign = client.get(f"/api/campaigns/{identifier}").json()
            if campaign["state"] == "completed" and not application.state.worker_gate.locked():
                return campaign
            time.sleep(0.01)
        pytest.fail(f"The synthetic campaign did not finish: {campaign}")

    with TestClient(application) as client:
        left = create_and_wait(client, "Earlier", ["healthy", "ignored_write"], [3, 7])
        right = create_and_wait(client, "Later", ["ignored_write", "healthy"], [7, 3])
        before = snapshot_bytes(application.state.storage)
        response = client.get(
            "/api/campaign-compare", params={"left": left["id"], "right": right["id"]}
        )
        assert response.status_code == 200
        report = response.json()
        assert report["comparable"] and report["summary"]["comparable_cases"] == 4
        assert report["summary"]["compared_tests"] == (16 if custom else 56)
        assert report["summary"]["new_failures"] == report["summary"]["resolved_failures"] == 0
        assert report["summary"]["persistent_failures"] == (0 if custom else 8)
        assert snapshot_bytes(application.state.storage) == before
