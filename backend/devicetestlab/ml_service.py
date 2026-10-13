"""History-only test ordering and an auditable, simulated-device experiment.

The persisted model is plain JSON coefficients, never an executable pickle.
Assertions in engine.py remain the oracle for every training/evaluation label.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .engine import TESTS, run_suite, test_definition_snapshot

SCHEMA_VERSION = 1
EXPERIMENT_SEED = 73129
TRAIN_CYCLES = 120
VALIDATION_CYCLES = 30
TEST_CYCLES = 60
MODEL_FILENAME = "logistic-v1.json"
NUMERIC_FEATURES = [
    "last_failure",
    "failure_rate_last_5",
    "failure_rate_last_20",
    "observed_verdicts",
    "runs_since_failure",
    "previous_mean_modeled_cost_ms",
]
METHODS = {
    "default": "Default order",
    "recent_first": "Recent failures first",
    "ml": "Logistic regression",
}
SIMULATION_NOTICE = (
    "All device results are executed against a software simulator. Discovery costs are "
    "modeled milliseconds, not measured physical-device or production test times. "
    "The experiment does not establish a benefit on real laboratory data."
)


def _ids() -> list[str]:
    return [test["id"] for test in TESTS]


def feature_names() -> list[str]:
    """Stable identity columns plus information observed before the current cycle."""
    return [f"test_id:{test_id}" for test_id in _ids()] + NUMERIC_FEATURES


def _test_fingerprint() -> str:
    return test_definition_snapshot()["sha256"]


def _valid_history(history: list[dict]) -> list[dict]:
    expected = set(_ids())
    fingerprint = _test_fingerprint()
    complete = []
    for run in history:
        if run.get("state") != "completed":
            continue
        if run.get("suite_kind", "standard") != "standard" or run.get("plan_snapshot"):
            continue
        snapshot = run.get("definition_snapshot")
        if not isinstance(snapshot, dict) or snapshot.get("sha256") != fingerprint:
            continue
        tests = run.get("tests", [])
        ids = [test.get("id") for test in tests]
        if len(ids) != len(expected) or set(ids) != expected:
            continue
        if any(test.get("status") not in {"pass", "fail", "error"} for test in tests):
            continue
        complete.append(run)
    return complete


def features_from_history(history: list[dict]) -> dict[str, list[float]]:
    """Never reads scenario, seed, trace, current results, or wall-clock timing."""
    ids = _ids()
    observations: dict[str, list[dict]] = {test_id: [] for test_id in ids}
    for run in _valid_history(history):
        for result in run["tests"]:
            if result["status"] in {"pass", "fail"}:
                observations[result["id"]].append(result)
    declared = {test["id"]: float(test["modeled_cost_ms"]) for test in TESTS}
    features = {}
    for index, test_id in enumerate(ids):
        past = observations[test_id]
        failures = [int(result["status"] == "fail") for result in past]
        costs = [
            float(result["modeled_cost_ms"])
            for result in past[-20:]
            if isinstance(result.get("modeled_cost_ms"), (int, float))
            and math.isfinite(result["modeled_cost_ms"])
            and result["modeled_cost_ms"] > 0
        ]
        last_failure_index = next((i for i in range(len(failures) - 1, -1, -1) if failures[i]), -1)
        recent_5, recent_20 = failures[-5:], failures[-20:]
        features[test_id] = [float(i == index) for i in range(len(ids))] + [
            float(failures[-1]) if failures else 0.0,
            sum(recent_5) / len(recent_5) if recent_5 else 0.0,
            sum(recent_20) / len(recent_20) if recent_20 else 0.0,
            float(len(failures)),
            float(len(failures) - 1 - last_failure_index),
            sum(costs) / len(costs) if costs else declared[test_id],
        ]
    return features


def _recent_order(features: dict[str, list[float]]) -> list[str]:
    # Use short-window evidence per prior modeled cost, then last outcome and long window.
    offset = len(TESTS)
    return sorted(
        _ids(),
        key=lambda test_id: (
            -features[test_id][offset + 1] / max(features[test_id][-1], 0.001),
            -features[test_id][offset],
            -features[test_id][offset + 2],
        ),
    )


def _probability(row: list[float], model: dict) -> float:
    score = model["intercept"] + sum(
        coefficient * (value - mean) / scale
        for value, coefficient, mean, scale in zip(
            row, model["coefficients"], model["means"], model["scales"], strict=True
        )
    )
    if score >= 0:
        return 1.0 / (1.0 + math.exp(-score))
    exponent = math.exp(score)
    return exponent / (1.0 + exponent)


def _model_order(features: dict[str, list[float]], model: dict) -> list[str]:
    return sorted(
        _ids(),
        key=lambda test_id: (
            -_probability(features[test_id], model) / max(features[test_id][-1], 0.001)
        ),
    )


def _read_model(model_dir: Path) -> dict:
    path = model_dir / MODEL_FILENAME
    if path.stat().st_size > 256_000:
        raise ValueError("model artifact exceeds the local schema limit")
    model = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(model, dict):
        raise ValueError("model artifact must be an object")
    if model.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported model version")
    if model.get("features") != feature_names():
        raise ValueError("model features differ from the current suite")
    if model.get("test_fingerprint") != _test_fingerprint():
        raise ValueError("model was trained against different test definitions")
    if model.get("classes") != [0, 1]:
        raise ValueError("model requires both PASS and FAIL training examples")
    for name in ("coefficients", "means", "scales"):
        vector = model.get(name)
        if not isinstance(vector, list) or len(vector) != len(feature_names()):
            raise ValueError(f"invalid model {name}")
        if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector):
            raise ValueError(f"invalid model {name}")
    if any(value <= 0 for value in model["scales"]):
        raise ValueError("invalid model scales")
    if not isinstance(model.get("intercept"), (int, float)):
        raise ValueError("invalid model intercept")
    if not math.isfinite(model["intercept"]):
        raise ValueError("invalid model intercept")
    return model


def prioritize(strategy: str, history: list[dict], model_dir: Path) -> tuple[list[str], str]:
    """Return a full permutation and explain use/fallback; never skip a test."""
    if strategy not in METHODS:
        raise ValueError(f"Unknown strategy: {strategy}")
    if strategy == "default":
        return _ids(), "Default order; no model used."
    completed = _valid_history(history)
    if not completed:
        return _ids(), "Fallback: no compatible completed history; using default order."
    features = features_from_history(completed)
    if strategy == "recent_first":
        return _recent_order(features), "History rule: recent failure rate per prior modeled cost."
    try:
        model = _read_model(Path(model_dir))
    except (OSError, ValueError, TypeError, KeyError):
        return _recent_order(features), (
            "Fallback: no usable model; using the recent-failures rule. "
            "Train or refresh the model in Ordering lab."
        )
    outcomes = {
        result["status"]
        for run in completed
        for result in run["tests"]
        if result["status"] in {"pass", "fail"}
    }
    if outcomes != {"pass", "fail"}:
        return _recent_order(features), (
            "Fallback: history has one verdict class; using history rule."
        )
    return _model_order(features, model), (
        "Trained logistic regression: predicted failure risk per prior modeled cost; full suite."
    )


def scenario_schedule(seed: int = EXPERIMENT_SEED) -> list[dict]:
    """Fixed before model fitting; the last block intentionally breaks persistence."""
    blocks = [
        ("train", "healthy", 12, "initial_healthy"),
        ("train", "ignored_write", 24, "persistent_defect"),
        ("train", "healthy", 8, "repaired"),
        ("train", "corrupt_read", 24, "persistent_defect"),
        ("train", "healthy", 8, "repaired"),
        ("train", "ignored_write", 16, "recurring_defect"),
        ("train", "stuck_state", 16, "different_defect"),
        ("train", "healthy", 12, "repaired"),
        ("validation", "corrupt_read", 8, "persistent_defect"),
        ("validation", "healthy", 4, "repaired"),
        ("validation", "stuck_state", 8, "persistent_defect"),
        ("validation", "healthy", 4, "repaired"),
        ("validation", "ignored_write", 6, "persistent_defect"),
        ("test", "timeout", 10, "unseen_defect"),
        ("test", "healthy", 6, "repaired"),
        ("test", "stuck_state", 10, "recurring_defect"),
        ("test", "healthy", 4, "repaired"),
    ]
    entries: list[dict[str, Any]] = []
    for phase, scenario, count, regime in blocks:
        for _ in range(count):
            entries.append({"phase": phase, "scenario": scenario, "regime": regime})
    rng = random.Random(seed)
    scenarios = ["healthy", "corrupt_read", "ignored_write", "stuck_state", "timeout"]
    for _ in range(30):
        entries.append(
            {
                "phase": "test",
                "scenario": rng.choice(scenarios),
                "regime": "independent_mixed_scenarios",
            }
        )
    for cycle, entry in enumerate(entries, start=1):
        entry.update(cycle=cycle, seed=seed + cycle)
    return entries


def _costs(results: list[dict]) -> tuple[float | None, float]:
    cumulative = 0.0
    first = None
    for result in results:
        cumulative += float(result["modeled_cost_ms"])
        if first is None and result["status"] == "fail":
            first = cumulative
    return first, cumulative


def _ordered_results(results: list[dict], order: list[str]) -> list[dict]:
    indexed = {result["id"]: result for result in results}
    return [indexed[test_id] for test_id in order]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, indent=2, allow_nan=False), encoding="utf-8", newline="\n"
    )
    temporary.replace(path)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n")


def _fit_model(rows: list[dict], regularization: float) -> dict:
    import sklearn
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from threadpoolctl import threadpool_limits

    labels = [row["label"] for row in rows]
    if set(labels) != {0, 1}:
        raise ValueError("Training requires both PASS and FAIL labels from executed tests.")
    matrix = [row["features"] for row in rows]
    with threadpool_limits(limits=1):
        scaler = StandardScaler()
        scaled = scaler.fit_transform(matrix)
        classifier = LogisticRegression(
            C=regularization, solver="liblinear", max_iter=500, random_state=EXPERIMENT_SEED
        )
        classifier.fit(scaled, labels)
    return {
        "schema_version": SCHEMA_VERSION,
        "model_name": "Logistic regression",
        "features": feature_names(),
        "test_ids": _ids(),
        "test_fingerprint": _test_fingerprint(),
        "classes": [int(value) for value in classifier.classes_],
        "coefficients": classifier.coef_[0].tolist(),
        "intercept": float(classifier.intercept_[0]),
        "means": scaler.mean_.tolist(),
        "scales": scaler.scale_.tolist(),
        "regularization_C": regularization,
        "sklearn_version": sklearn.__version__,
        "solver": "liblinear",
        "fit_cycles": [1, TRAIN_CYCLES],
        "training_rows": len(rows),
        "training_class_counts": {"pass": labels.count(0), "fail": labels.count(1)},
        "ranking": "predicted failure probability / previous mean modeled cost",
    }


def _verify_equivalent(reference: list[dict], comparison: list[dict]) -> None:
    def signature(results: list[dict]) -> dict:
        return {
            item["id"]: (item["status"], item["expected"], item["actual"], item["modeled_cost_ms"])
            for item in results
        }

    if len(comparison) != len(TESTS) or signature(reference) != signature(comparison):
        raise RuntimeError("Ordering changed full-suite results; experiment cannot be compared.")


def run_experiment(output_dir: Path, seed: int = EXPERIMENT_SEED) -> dict:
    """Execute predetermined cohorts, fit on the past, and replay every final strategy."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    overall_start = time.perf_counter()
    created_at = datetime.now(UTC).isoformat()
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2147483437:
        raise ValueError("Experiment seed must leave room for all 210 cycle seeds.")
    schedule = scenario_schedule(seed)
    history: list[dict] = []
    definition_snapshot = test_definition_snapshot()
    rows: list[dict] = []
    cycles: list[dict] = []
    # The full catalog is defined first. It is ground truth, never a predictor input.
    for entry in schedule:
        features = features_from_history(history)
        results = run_suite(entry["scenario"], entry["seed"])
        if any(result["status"] not in {"pass", "fail"} for result in results):
            raise RuntimeError("Runner error in experiment; no device label was fabricated.")
        for result in results:
            rows.append(
                {
                    "cycle": entry["cycle"],
                    "phase": entry["phase"],
                    "test_id": result["id"],
                    "features": features[result["id"]],
                    "label": int(result["status"] == "fail"),
                }
            )
        cycles.append({"entry": entry, "features": features, "results": results})
        history.append(
            {
                "state": "completed",
                "cycle": entry["cycle"],
                "tests": results,
                "suite_kind": "standard",
                "definition_snapshot": definition_snapshot,
            }
        )

    training_rows = [row for row in rows if row["phase"] == "train"]
    fit_start = time.perf_counter()
    candidates = [_fit_model(training_rows, value) for value in (0.1, 1.0)]
    training_seconds = time.perf_counter() - fit_start
    validation = [cycle for cycle in cycles if cycle["entry"]["phase"] == "validation"]
    candidate_scores = []
    for model in candidates:
        costs = []
        for cycle in validation:
            ordered = _ordered_results(cycle["results"], _model_order(cycle["features"], model))
            first, _ = _costs(ordered)
            if first is not None:
                costs.append(first)
        candidate_scores.append(_mean(costs))
    chosen = min(
        range(len(candidates)),
        key=lambda i: (candidate_scores[i] if candidate_scores[i] is not None else math.inf, i),
    )
    model = candidates[chosen]
    model["created_at"] = created_at

    measurements: dict[str, list[dict]] = {method: [] for method in METHODS}
    comparison_runs = []
    cases = []
    # Final data is never used to choose C. The chosen model is now frozen.
    for cycle in cycles:
        entry = cycle["entry"]
        if entry["phase"] != "test":
            continue
        orders = {
            "default": _ids(),
            "recent_first": _recent_order(cycle["features"]),
            "ml": _model_order(cycle["features"], model),
        }
        case = {
            "cycle": entry["cycle"],
            "phase": "test",
            "scenario": entry["scenario"],
            "regime": entry["regime"],
        }
        for method, order in orders.items():
            results = run_suite(entry["scenario"], entry["seed"], order=order)
            _verify_equivalent(cycle["results"], results)
            first, total = _costs(results)
            measurements[method].append(
                {
                    "first": first,
                    "total": total,
                    "failures": sum(result["status"] == "fail" for result in results),
                    "tests": len(results),
                }
            )
            case[f"{method}_first_failure_cost_ms"] = first
            comparison_runs.append(
                {
                    "cycle": entry["cycle"],
                    "strategy": method,
                    "test_order": order,
                    "tests": results,
                }
            )
        cases.append(case)
    methods = []
    for method, observations in measurements.items():
        first_costs = [obs["first"] for obs in observations if obs["first"] is not None]
        methods.append(
            {
                "id": method,
                "label": METHODS[method],
                "mean_first_failure_cost_ms": _mean(first_costs),
                "mean_total_cost_ms": _mean([obs["total"] for obs in observations]),
                "faulty_cycles": len(first_costs),
                "clean_cycles": len(observations) - len(first_costs),
                "failures_found": sum(obs["failures"] for obs in observations),
                "total_tests": sum(obs["tests"] for obs in observations),
            }
        )
    comparable = [method for method in methods if method["mean_first_failure_cost_ms"] is not None]
    best = (
        min(comparable, key=lambda item: item["mean_first_failure_cost_ms"]) if comparable else None
    )
    findings = [
        f"All three methods executed all {len(TESTS)} tests in every final evaluation cycle; "
        "verdicts, observations and modeled total costs matched for each cycle.",
        "Discovery averages exclude cycles with no observed failure; "
        "those cycles are counted separately.",
        "The final cohort introduces timeout behavior unseen during fitting and validation, "
        "then ends with independent mixed scenarios where history is less informative.",
    ]
    if best:
        findings.append(
            f"Lowest mean modeled cost to first failure: {best['label']} "
            f"({best['mean_first_failure_cost_ms']:.3f} modeled ms). "
            "This is an observed simulation result, not a promised ML gain."
        )
    report = {
        "state": "completed",
        "schema_version": SCHEMA_VERSION,
        "created_at": created_at,
        "model_name": "Logistic regression",
        "simulation_notice": SIMULATION_NOTICE,
        "split": {
            "train_cycles": TRAIN_CYCLES,
            "validation_cycles": VALIDATION_CYCLES,
            "test_cycles": TEST_CYCLES,
        },
        "dataset": {
            "cycles": len(schedule),
            "rows": len(rows),
            "seed": seed,
            "features": feature_names(),
        },
        "methods": methods,
        "cases": cases,
        "findings": findings,
        "limitations": [
            "The scenario schedule and device are synthetic; real-lab transfer is unmeasured.",
            "A defect-free history cannot predict a genuinely new defect with certainty.",
            "Test order changes feedback timing, not the work of executing the full suite.",
            "The simple rule may beat ML; two fixed C values are the entire tuning budget.",
            "Only the selected fault scenarios are exercised; this is not a coverage percentage.",
            "Failure probability scores are not independently calibrated confidence estimates.",
        ],
        "training_seconds": training_seconds,
        "validation": {
            "candidate_C": [0.1, 1.0],
            "mean_first_failure_cost_ms": candidate_scores,
            "selected_C": model["regularization_C"],
            "tie_break": "First candidate; no final-test tuning.",
        },
        "model_artifact": f"model/{MODEL_FILENAME}",
        "sklearn_version": model["sklearn_version"],
        "cost_definition": "Sum of modeled test costs through the first failing test, inclusive.",
        "wall_time_note": (
            "Training seconds include model imports and both candidate fits. Experiment seconds "
            "cover data execution, fitting and final replays before artifact output. "
            "Both are measured on this host; modeled costs do not sleep."
        ),
        "experiment_seconds": time.perf_counter() - overall_start,
    }
    # Publish a new model only after the complete comparison has passed verification.
    _write_json(output_dir / "model" / MODEL_FILENAME, model)
    _write_json(output_dir / "ground_truth.json", {"seed": seed, "schedule": schedule})
    _write_jsonl(output_dir / "dataset.jsonl", rows)
    _write_jsonl(output_dir / "execution_history.jsonl", history)
    _write_jsonl(output_dir / "comparison_runs.jsonl", comparison_runs)
    _write_json(output_dir / "report.json", report)
    artifact_names = [
        "ground_truth.json",
        "dataset.jsonl",
        "execution_history.jsonl",
        "comparison_runs.jsonl",
        "report.json",
        f"model/{MODEL_FILENAME}",
    ]
    _write_json(
        output_dir / "manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "created_at": created_at,
            "test_fingerprint": _test_fingerprint(),
            "test_definitions": test_definition_snapshot(),
            "sklearn_version": model["sklearn_version"],
            "cpu_threads": 1,
            "label_source": (
                "Actual engine.run_suite assertion results, never the scenario identifier."
            ),
            "feature_boundary": "Features frozen from completed earlier cycles before execution.",
            "final_evaluation": "Frozen fitted model; history updates after each complete cycle.",
            "artifacts": {
                name: hashlib.sha256((output_dir / name).read_bytes()).hexdigest()
                for name in artifact_names
            },
        },
    )
    return report
