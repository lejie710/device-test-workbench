"""Check the fixed protocol and regime denominators without inventing outcome claims."""

import json

from devicetestlab.ml_robustness import PROTOCOL_ID, SEEDS, protocol, regime_results
from devicetestlab.ml_service import scenario_schedule


def test_protocol_keeps_all_predeclared_seeds_and_original_tuning_budget():
    design = protocol()
    assert design["id"] == PROTOCOL_ID
    assert design["dataset_seeds"] == [73129, 91283, 105613]
    assert design["candidate_C"] == [0.1, 1.0]
    assert design["v1_preserved"]
    schedules = [scenario_schedule(seed) for seed in SEEDS]
    assert all(len(schedule) == 210 for schedule in schedules)
    assert all(
        all(case["scenario"] != "timeout" for case in schedule[:150]) for schedule in schedules
    )
    assert len({tuple(case["scenario"] for case in schedule[180:]) for schedule in schedules}) == 3


def test_regime_summary_counts_clean_cycles_without_fake_zero_first_failure(tmp_path):
    # Explicit small observation fixture checks denominator semantics, not model accuracy.
    report = {
        "cases": [
            {"cycle": 1, "regime": "repaired"},
            {"cycle": 2, "regime": "mixed"},
            {"cycle": 3, "regime": "mixed"},
        ]
    }
    observations = [
        {"cycle": 1, "strategy": "default", "tests": [{"status": "pass", "modeled_cost_ms": 5}]},
        {"cycle": 2, "strategy": "default", "tests": [{"status": "fail", "modeled_cost_ms": 7}]},
        {"cycle": 3, "strategy": "default", "tests": [{"status": "pass", "modeled_cost_ms": 3}]},
    ]
    (tmp_path / "comparison_runs.jsonl").write_text(
        "\n".join(json.dumps(row) for row in observations), encoding="utf-8"
    )
    regimes = {item["regime"]: item["methods"][0] for item in regime_results(report, tmp_path)}
    assert regimes["repaired"]["mean_first_failure_cost_ms"] is None
    assert regimes["mixed"]["mean_first_failure_cost_ms"] == 7
    assert regimes["mixed"]["mean_total_cost_ms"] == 5
    assert regimes["mixed"]["faulty_cycles"] == regimes["mixed"]["clean_cycles"] == 1
