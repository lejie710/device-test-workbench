"""Plans are data, with stable snapshots and independent device observations."""

import copy
import json
from pathlib import Path

import pytest
from devicetestlab.plans import TestPlan as Plan
from devicetestlab.plans import plan_snapshot, run_plan
from pydantic import ValidationError


def example():
    return json.loads(
        (Path(__file__).parents[1] / "examples/plans/byte-roundtrip.json").read_text()
    )


def test_plan_executes_the_independent_device_and_keeps_complete_trace():
    snapshot = plan_snapshot(Plan.model_validate(example()))
    assert run_plan(snapshot, "healthy", 3)[0]["status"] == "pass"
    failed = run_plan(snapshot, "ignored_write", 3)[0]
    assert failed["status"] == "fail"
    assert failed["actual"] == {"ok": True, "value": 0}
    assert failed["expected"] == {"ok": True, "value": 165}
    assert len(failed["trace"]) == 4


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(schema_version="2.0"),
        lambda p: p.update(path="server-file"),
        lambda p: p["tests"].append(copy.deepcopy(p["tests"][0])),
        lambda p: p["tests"][0].update(id="valid_id\n"),
        lambda p: p["tests"][0]["steps"][0]["command"].update(op="EXEC"),
        lambda p: p["tests"][0]["steps"][0]["command"].update(address=True),
        lambda p: p["tests"][0]["steps"][0]["command"].update(value=10000),
        lambda p: p["tests"][0]["steps"][0]["expected"].update(ok=1),
        lambda p: p["tests"][0].update(steps=p["tests"][0]["steps"] * 17),
    ],
)
def test_invalid_or_executable_plan_input_is_rejected(mutation):
    plan = example()
    mutation(plan)
    with pytest.raises(ValidationError):
        Plan.model_validate(plan)


def test_snapshot_is_canonical_and_detached_from_editable_input():
    raw = example()
    snapshot = plan_snapshot(Plan.model_validate(raw))
    same = json.loads(json.dumps(raw, sort_keys=True))
    assert snapshot["sha256"] == plan_snapshot(Plan.model_validate(same))["sha256"]
    raw["tests"][0]["steps"][1]["expected"]["value"] = 12
    assert snapshot["plan"]["tests"][0]["steps"][1]["expected"]["value"] == 165
    assert snapshot["sha256"] != plan_snapshot(Plan.model_validate(raw))["sha256"]


def test_custom_tests_start_with_fresh_state_and_wrong_oracle_does_not_change_device():
    raw = example()
    raw["tests"][0]["steps"] = raw["tests"][0]["steps"][:1]
    raw["tests"].append(
        {
            "id": "fresh",
            "name": "Fresh state",
            "steps": [
                {"command": {"op": "READ", "address": 3}, "expected": {"ok": True, "value": 0}}
            ],
        }
    )
    results = run_plan(plan_snapshot(Plan.model_validate(raw)), "healthy", 3)
    assert all(test["status"] == "pass" for test in results)
    raw["tests"][1]["steps"][0]["expected"]["value"] = 90
    result = run_plan(plan_snapshot(Plan.model_validate(raw)), "healthy", 3)[1]
    assert result["status"] == "fail" and result["actual"]["value"] == 0


def test_boundary_negative_commands_are_supported_as_data():
    raw = example()
    raw["tests"][0]["steps"] = [
        {
            "command": {"op": "READ", "address": -1},
            "expected": {"ok": False, "error": "INVALID_ADDRESS"},
        }
    ]
    assert run_plan(plan_snapshot(Plan.model_validate(raw)), "healthy", 0)[0]["status"] == "pass"
