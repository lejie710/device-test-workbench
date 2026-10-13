"""Check the ordering experiment's evidence and information boundaries."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest
from devicetestlab import ml_service as ml
from devicetestlab.engine import TESTS, run_suite
from devicetestlab.engine import test_definition_snapshot as definition_snapshot


def completed(scenario="healthy", seed=731):
    return {
        "state": "completed",
        "tests": run_suite(scenario, seed),
        "definition_snapshot": definition_snapshot(),
    }


@pytest.fixture(scope="module")
def experiment(tmp_path_factory):
    directory = tmp_path_factory.mktemp("ml-experiment")
    real_fit = ml._fit_model
    observed_fit_calls = []

    def checked_fit(rows, regularization):
        assert {row["phase"] for row in rows} == {"train"}
        assert min(row["cycle"] for row in rows) == 1
        assert max(row["cycle"] for row in rows) == 120
        observed_fit_calls.append(regularization)
        return real_fit(rows, regularization)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ml, "_fit_model", checked_fit)
        report = ml.run_experiment(directory)
    assert observed_fit_calls == [0.1, 1.0]
    return directory, report


def test_features_ignore_scenarios_seeds_traces_and_current_timing():
    history = [completed(), completed("ignored_write")]
    changed = copy.deepcopy(history)
    for run in changed:
        run.update(scenario="secret-fault", seed=999, future_label=1)
        for result in run["tests"]:
            result.update(duration_ms=987654, trace=[{"secret": "fault answer"}], actual="secret")
    assert ml.features_from_history(history) == ml.features_from_history(changed)
    names = ml.feature_names()
    assert not any("scenario" in name or "seed" in name or "duration" in name for name in names)


def test_incomplete_history_cannot_supply_labels():
    history = [completed("corrupt_read")]
    history[0]["state"] = "running"
    assert ml.features_from_history(history) == ml.features_from_history([])
    history[0]["state"] = "completed"
    history[0]["tests"].pop()
    assert ml.features_from_history(history) == ml.features_from_history([])


def test_runner_errors_are_not_device_training_verdicts():
    history = [completed()]
    history[0]["tests"][0]["status"] = "error"
    features = ml.features_from_history(history)
    offset = len(TESTS)
    assert features[TESTS[0]["id"]][offset + 3] == 0
    assert features[TESTS[1]["id"]][offset + 3] == 1


def test_missing_model_and_empty_history_have_explicit_full_suite_fallback(tmp_path):
    ids = [test["id"] for test in TESTS]
    order, status = ml.prioritize("ml", [], tmp_path)
    assert order == ids and "no compatible completed history" in status
    order, status = ml.prioritize("ml", [completed("stuck_state")], tmp_path)
    assert set(order) == set(ids) and len(order) == len(ids)
    assert "Fallback" in status and "no usable model" in status
    with pytest.raises(ValueError, match="Unknown strategy"):
        ml.prioritize("skip-failing-tests", [], tmp_path)


def test_schedule_is_fixed_and_new_fault_is_held_out():
    schedule = ml.scenario_schedule()
    assert schedule == ml.scenario_schedule()
    assert len(schedule) == 210
    assert [entry["phase"] for entry in schedule] == (
        ["train"] * 120 + ["validation"] * 30 + ["test"] * 60
    )
    assert all(entry["scenario"] != "timeout" for entry in schedule[:150])
    assert schedule[150]["scenario"] == "timeout"
    assert all(entry["regime"] == "independent_mixed_scenarios" for entry in schedule[-30:])


def test_features_are_frozen_before_current_cycle_and_labels_come_from_engine(experiment):
    directory, _ = experiment
    rows = [json.loads(line) for line in (directory / "dataset.jsonl").read_text().splitlines()]
    history = [
        json.loads(line)
        for line in (directory / "execution_history.jsonl").read_text().splitlines()
    ]
    width = len(TESTS)
    # Check a clean cycle, first defective cycle, train boundary and unseen-defect cycle.
    for cycle_index in (0, 12, 119, 150, 209):
        expected_features = ml.features_from_history(history[:cycle_index])
        verdicts = {item["id"]: item["status"] for item in history[cycle_index]["tests"]}
        for row in rows[cycle_index * width : (cycle_index + 1) * width]:
            assert row["features"] == expected_features[row["test_id"]]
            assert row["label"] == int(verdicts[row["test_id"]] == "fail")
            assert set(row) == {"cycle", "phase", "test_id", "features", "label"}


def test_final_comparison_runs_every_test_and_preserves_verdicts(experiment):
    directory, report = experiment
    assert report["state"] == "completed"
    assert report["dataset"]["rows"] == 210 * len(TESTS)
    assert report["split"] == {"train_cycles": 120, "validation_cycles": 30, "test_cycles": 60}
    assert len(report["cases"]) == 60
    assert len({method["failures_found"] for method in report["methods"]}) == 1
    assert len({method["mean_total_cost_ms"] for method in report["methods"]}) == 1
    for method in report["methods"]:
        assert method["total_tests"] == 60 * len(TESTS)
        assert method["faulty_cycles"] + method["clean_cycles"] == 60
        assert method["clean_cycles"] > 0
    replays = [
        json.loads(line) for line in (directory / "comparison_runs.jsonl").read_text().splitlines()
    ]
    assert len(replays) == 180
    ids = {test["id"] for test in TESTS}
    for replay in replays:
        assert len(replay["tests"]) == len(ids)
        assert set(replay["test_order"]) == ids
    for case in report["cases"]:
        if case["scenario"] == "healthy":
            assert all(case[f"{method}_first_failure_cost_ms"] is None for method in ml.METHODS)


def test_model_is_trained_only_on_earlier_cycles_and_is_plain_json(experiment):
    directory, report = experiment
    model = ml._read_model(directory / "model")
    assert model["fit_cycles"] == [1, 120]
    assert model["training_rows"] == 120 * len(TESTS)
    assert model["regularization_C"] in (0.1, 1.0)
    assert model["sklearn_version"] == report["sklearn_version"]
    assert list((directory / "model").iterdir()) == [directory / "model" / ml.MODEL_FILENAME]
    history = [completed(), completed("corrupt_read")]
    order, status = ml.prioritize("ml", history, directory / "model")
    assert len(order) == len(TESTS) and len(set(order)) == len(TESTS)
    assert status.startswith("Trained logistic regression")
    _, status = ml.prioritize("ml", [completed()], directory / "model")
    assert "one verdict class" in status


def test_manifest_hashes_all_evidence_and_model(experiment):
    directory, _ = experiment
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["cpu_threads"] == 1
    assert manifest["test_definitions"]["sha256"] == manifest["test_fingerprint"]
    for name, expected in manifest["artifacts"].items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected


def test_invalid_model_is_rejected_without_executing_it(experiment, tmp_path):
    directory, _ = experiment
    model = ml._read_model(directory / "model")
    model["scales"][0] = 0
    (tmp_path / ml.MODEL_FILENAME).write_text(json.dumps(model))
    with pytest.raises(ValueError, match="invalid model scales"):
        ml._read_model(tmp_path)
    order, status = ml.prioritize("ml", [completed("stuck_state")], tmp_path)
    assert len(order) == len(TESTS)
    assert "Fallback" in status and "no usable model" in status
    assert str(tmp_path) not in status


def test_one_class_training_is_refused():
    with pytest.raises(ValueError, match="both PASS and FAIL"):
        ml._fit_model([{"features": [0.0] * len(ml.feature_names()), "label": 0}], 1.0)


def test_experiment_refuses_runner_error_instead_of_fabricating_a_label(monkeypatch, tmp_path):
    results = run_suite("healthy", 1)
    results[0]["status"] = "error"
    monkeypatch.setattr(ml, "run_suite", lambda *args, **kwargs: results)
    with pytest.raises(RuntimeError, match="no device label was fabricated"):
        ml.run_experiment(tmp_path)
    assert not (tmp_path / "model" / ml.MODEL_FILENAME).exists()


@pytest.mark.parametrize("change", ["custom", "plan_snapshot", "different_definition", "missing"])
def test_custom_or_incompatible_history_cannot_influence_standard_suite(change):
    history = [completed("corrupt_read")]
    if change == "custom":
        history[0]["suite_kind"] = "custom"
    elif change == "plan_snapshot":
        history[0]["plan_snapshot"] = {"kind": "custom"}
    elif change == "different_definition":
        history[0]["definition_snapshot"]["sha256"] = "different"
    else:
        del history[0]["definition_snapshot"]
    assert ml.features_from_history(history) == ml.features_from_history([])


def test_v1_history_without_suite_kind_remains_compatible():
    history = [completed("corrupt_read")]
    assert "suite_kind" not in history[0]
    assert ml._valid_history(history) == history


def test_evidence_writers_use_lf_bytes_for_portable_hashes(tmp_path):
    payload = {"data": [1, 2], "label": "observed"}
    document, lines = tmp_path / "report.json", tmp_path / "observations.jsonl"
    ml._write_json(document, payload)
    ml._write_jsonl(lines, [payload, payload])
    assert b"\r\n" not in document.read_bytes()
    assert document.read_bytes() == json.dumps(payload, indent=2).encode("utf-8")
    assert b"\r\n" not in lines.read_bytes()
    assert lines.read_bytes().count(b"\n") == 2
    assert lines.read_bytes().endswith(b"\n")
