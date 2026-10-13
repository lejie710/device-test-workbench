# Fixed three-seed robustness protocol

This fixed protocol was defined before its results were examined. It extends the original simulation experiment without changing or replacing the dated V1 result.

## Fixed design

- Protocol identifier: `history-ordering-three-seed-v1.1`.
- Dataset seeds: **73129, 91283, 105613**, all retained regardless of the observed winner.
- Each seed executes the same 210-cycle cohort design: 120 training, 30 validation and 60 final cycles. The seed changes fault addresses and the independently mixed final scenarios.
- The learner's solver seed remains fixed at 73129, separating scenario variation from incidental solver randomness.
- The two existing regularization candidates remain `C=0.1` and `C=1.0`. Validation chooses between them; the final block never chooses a model, feature set or seed.
- Feature definitions, chronological boundaries, complete-suite execution and the default/history-rule/ML comparisons remain the same as the V1 experiment. No ablation is included in this bounded extension.
- Each run writes its actual execution evidence and model under a separate seed directory. The original V1 benchmark and runtime model are not overwritten.

## Reporting units

Report each seed independently. Within its final cohort, also report the predefined regimes: an unseen fault, repair, recurrent fault, and independent mixed scenarios. Average first-failure cost only over cycles with an observed failing test; count clean cycles separately and use null if a group contains no failure. Report full-suite modeled cost, executed-test counts and failing-test observations alongside discovery cost.

Across seeds, show the mean and minimum/maximum of the three per-seed discovery means for each method. These are descriptive sensitivity results, not a confidence interval. A cycle contains correlated test observations, and three deterministic synthetic cohorts do not represent three independent physical devices or a manufacturing population. Do not treat individual test rows as independent samples to inflate confidence.

Report the observed winner for every seed, including ties or a loss by ML. A positive `ml_minus_recent_first_ms` means ML found the first failing test later than the history rule. There is no universal acceptable threshold, production-gain claim or automatic root-cause verdict.

## Reproduce and inspect

Run `python -m devicetestlab.ml_robustness --output runtime/robustness-v1.1` using the project environment. The report, protocol copy and hash manifest are stored with the seed-specific model, dataset and execution files. This does not train or replace the model used by the interactive test bench.

The implementation is `backend/devicetestlab/ml_robustness.py`; the original experiment remains in `ml_service.py`. Read `docs/ML_EXPERIMENT.md` first for the feature boundary and assertion-derived labels. The published result is copied to `docs/results/robustness-v1.1.json` after verification.
