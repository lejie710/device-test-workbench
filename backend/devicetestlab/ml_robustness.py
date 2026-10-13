"""A predefined three-seed sensitivity study, stored separately from the V1 benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .ml_service import METHODS, _costs, _mean, _write_json, run_experiment

PROTOCOL_ID = "history-ordering-three-seed-v1.1"
SEEDS = (73129, 91283, 105613)


def protocol() -> dict:
    return {
        "id": PROTOCOL_ID,
        "dataset_seeds": list(SEEDS),
        "learner_seed": 73129,
        "train_cycles": 120,
        "validation_cycles": 30,
        "test_cycles": 60,
        "candidate_C": [0.1, 1.0],
        "ranking": "failure risk / previous modeled cost",
        "selection": "Validation only; fitted weights frozen throughout final evaluation.",
        "comparison_unit": "Complete final cycles, summarized separately for each seed.",
        "ablation": "None",
        "v1_preserved": True,
    }


def regime_results(report: dict, directory: Path) -> list[dict]:
    cases = {case["cycle"]: case for case in report["cases"]}
    groups: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    with (directory / "comparison_runs.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            run = json.loads(line)
            regime = cases[run["cycle"]]["regime"]
            first, total = _costs(run["tests"])
            groups[regime][run["strategy"]].append(
                {
                    "first": first,
                    "total": total,
                    "tests": len(run["tests"]),
                    "failures": sum(test["status"] == "fail" for test in run["tests"]),
                }
            )
    output = []
    for regime, strategies in groups.items():
        methods = []
        for method, observations in strategies.items():
            firsts = [item["first"] for item in observations if item["first"] is not None]
            methods.append(
                {
                    "id": method,
                    "label": METHODS[method],
                    "cycles": len(observations),
                    "faulty_cycles": len(firsts),
                    "clean_cycles": len(observations) - len(firsts),
                    "mean_first_failure_cost_ms": _mean(firsts),
                    "mean_total_cost_ms": _mean([item["total"] for item in observations]),
                    "failures_found": sum(item["failures"] for item in observations),
                    "total_tests": sum(item["tests"] for item in observations),
                }
            )
        output.append({"regime": regime, "methods": methods})
    return output


def run_robustness(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    # Persist the fixed design before invoking any experiment or seeing its outcomes.
    _write_json(output_dir / "protocol.json", protocol())
    trials = []
    for seed in SEEDS:
        directory = output_dir / f"seed-{seed}"
        report = run_experiment(directory, seed=seed)
        metrics = {item["id"]: item["mean_first_failure_cost_ms"] for item in report["methods"]}
        best = min(value for value in metrics.values() if value is not None)
        trials.append(
            {
                "seed": seed,
                "methods": report["methods"],
                "split": report["split"],
                "dataset": report["dataset"],
                "validation": report["validation"],
                "regimes": regime_results(report, directory),
                "winner_ids": [key for key, value in metrics.items() if value == best],
                "ml_minus_recent_first_ms": metrics["ml"] - metrics["recent_first"],
                "evidence_directory": directory.name,
            }
        )
    aggregate = []
    for method in METHODS:
        means = [
            next(
                item["mean_first_failure_cost_ms"]
                for item in trial["methods"]
                if item["id"] == method
            )
            for trial in trials
        ]
        aggregate.append(
            {
                "id": method,
                "label": METHODS[method],
                "seed_count": len(means),
                "mean_of_seed_means_ms": _mean(means),
                "minimum_seed_mean_ms": min(means),
                "maximum_seed_mean_ms": max(means),
                "winning_seeds": sum(method in trial["winner_ids"] for trial in trials),
            }
        )
    report = {
        "state": "completed",
        "created_at": datetime.now(UTC).isoformat(),
        "protocol": protocol(),
        "trials": trials,
        "aggregate": aggregate,
        "findings": [
            f"Executed {len(SEEDS)} fixed seeds with 60 held-out cycles each; "
            "all methods retained the complete suite and equivalent per-cycle observations.",
            "Positive ML-minus-rule values mean later feedback from ML. All seeds are retained.",
            "Repair regimes with no failure have null first-failure means, not zero.",
        ],
        "limitations": [
            "Three synthetic cohorts are a sensitivity study, not a confidence interval.",
            "Correlated test rows are not independent physical devices or production samples.",
            "The model-selection budget is unchanged; final results were not used for tuning.",
            "Wall-clock measurements are excluded from modeled-cost comparisons.",
            "The original V1 report and interactive model were not overwritten.",
        ],
    }
    _write_json(output_dir / "report.json", report)
    artifacts = ["protocol.json", "report.json"] + [f"seed-{seed}/manifest.json" for seed in SEEDS]
    source_dir = Path(__file__).resolve().parent
    _write_json(
        output_dir / "manifest.json",
        {
            "protocol_id": PROTOCOL_ID,
            "artifacts": {
                name: hashlib.sha256((output_dir / name).read_bytes()).hexdigest()
                for name in artifacts
            },
            "sources": {
                name: hashlib.sha256((source_dir / name).read_bytes()).hexdigest()
                for name in ("ml_robustness.py", "ml_service.py", "engine.py", "device.py")
            },
        },
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("runtime/robustness-v1.1"))
    report = run_robustness(parser.parse_args().output)
    print(json.dumps({"protocol": PROTOCOL_ID, "aggregate": report["aggregate"]}, indent=2))


if __name__ == "__main__":
    main()
