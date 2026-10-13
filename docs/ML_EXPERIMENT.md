# The ordering experiment

Device Test Lab uses a small local logistic-regression model to recommend a test order. It never changes assertions, skips a test, or declares a device healthy. A valid outcome is that a simple history rule beats ML.

## What is actually executed

The experiment calls the same `engine.run_suite` used by the test bench. The device responds to WRITE, READ, RESET and STATUS commands. The engine compares the responses with its independent test expectations and records PASS, FAIL or a runner ERROR. Only executed PASS/FAIL observations become labels. A runner ERROR aborts the experiment instead of becoming a fabricated device result.

Each test begins with a fresh simulated device. Every final comparison re-executes every test under each of the three orders. The experiment verifies identical per-test verdicts, decisive expected/actual observations and modeled costs across the orders.

The simulator has 14 tests in the initial suite. That count is a catalog size, not a percentage of physical-device fault coverage.

## Features: information available before execution

Each row contains a stable one-hot test identity followed by six numeric features:

| Feature | Definition and reason |
|---|---|
| `last_failure` | Previous observed verdict, with FAIL encoded as 1 and PASS as 0; provides short-term evidence. |
| `failure_rate_last_5` | Fraction of failures in up to five previous observed verdicts; tracks recent recurrence. |
| `failure_rate_last_20` | Fraction in up to twenty earlier verdicts; provides a longer context. |
| `observed_verdicts` | Number of prior PASS/FAIL observations for the test; distinguishes cold start from established history. |
| `runs_since_failure` | Number of observed verdicts after the latest failure; if none failed, equals the history length. |
| `previous_mean_modeled_cost_ms` | Mean modeled cost of up to twenty previous executions; supports ranking quick, informative tests first. Without observations, the declared static test cost is used. |

All features for a cycle are frozen before executing that cycle. Its observed labels are added afterwards, and its completed observations may influence the next cycle. No injected fault identifier, fault address, scenario name, seed, current response, current verdict or measured wall-clock timing enters the feature vector. Test identity is known before execution; it is not the hidden fault identity.

Completed history must contain the complete test catalog. Incomplete runs are excluded. Runner ERROR observations do not become either PASS or FAIL history for a test.

The V1.1 compatibility gate also excludes custom plans and records whose saved definition fingerprint is missing or differs from the standard catalog. Older V1 standard runs remain usable because they already preserved the canonical definition snapshot, even though they did not have a `suite_kind` field. Newly generated experiment history carries that same snapshot. A matching test ID alone is insufficient evidence that two tests have the same commands and expectations.

## Chronological cohorts

The schedule is defined in code before any model fitting, with seed 73129. It has 210 cycles: 120 training, 30 validation and 60 final evaluation. Each complete cycle produces one labeled row per test. These sizes keep the experiment inspectable and inexpensive; they are not a statistical power calculation or a guarantee of performance.

The training cohort contains healthy blocks, persistent ignored writes and corrupted reads, repairs, recurrence, and a stuck reset state. Validation includes later instances of the previously seen fault families. The final cohort begins with a response timeout family absent from training and validation, includes repairs and recurrence, and ends with thirty independently selected mixed scenarios. The latter deliberately weakens the usefulness of past outcomes.

The whole feature-and-label history is generated first for an inspectable replay dataset. Chronological separation is still enforced: only training rows are passed to the scaler and classifier, validation chooses between two fixed regularization strengths, and the final block is never used for fitting or selection. Features of a row were produced before that row's actual execution. The final cohort's labels are read only to assess its predictions and as already-observed history for subsequent cycles.

This is a controlled simulation experiment, not a representative sample of manufacturing failures. Independent random defects may have no predictable relationship with history. The scenario schedule is never tuned to produce an ML win.

## Model and selection

The implementation uses scikit-learn `StandardScaler` and `LogisticRegression`, with the `liblinear` solver and a fixed seed. Both preprocessing and model fitting use training rows only. Two fixed `C` candidates, 0.1 and 1.0, form the entire model-selection budget. Validation chooses the candidate with the lowest mean modeled cost to first failure on validation cycles that actually fail; ties select the first candidate. The selected model is not refitted on validation or final data.

The score is predicted failure risk divided by the previous mean modeled test cost. This is a scheduling heuristic, not a proof of an optimal order. A model score is also not a calibrated certainty that a test will fail. Numerical work during fitting is limited to one CPU thread.

The fitted model is saved as plain JSON containing coefficients, scaling parameters, feature names, class labels, the scikit-learn version, training counts and a fingerprint of the executed test definitions. Inference evaluates these coefficients directly. The application does not load arbitrary executable model objects or accept uploaded models.

## Comparison and fallbacks

| Method | Ordering |
|---|---|
| Default | The published catalog order. |
| Recent failures first | Recent five-verdict failure rate divided by previous modeled cost; ties use last outcome, longer history, then catalog order. |
| ML | Logistic-regression failure risk divided by previous modeled cost, then catalog order. |

Every method receives equivalent earlier history. The fitted ML weights remain frozen throughout final evaluation. All methods execute the full suite.

In the interactive bench, no completed history means default order. A missing, corrupt, outdated or incompatible model means the history rule. A single-class live history also uses the history rule. Every fallback is returned as a visible status. Training itself refuses a dataset containing only PASS or only FAIL labels.

## Reading the result

The primary metric is modeled cost to first failing test, including the cost of that failing test. A clean cycle has no first failure, so its value is null and it is excluded from the discovery-time average. Clean and faulty cycle counts are reported separately. The complete suite's modeled cost and discovered failing-test count are also reported and checked for equivalence across strategies.

The modeled costs use nominal command weights and charge the response deadline for a timed-out read. They are not calibrated hardware latencies. Previous observed costs can therefore differ from the healthy catalog estimate, and the scheduler uses only those earlier observations. The runner does not sleep to simulate them. Measured Python execution durations, model training seconds and experiment wall time are distinct measurements and must never be presented as physical-device test performance. Reordering a complete suite aims to provide earlier feedback; it does not reduce the total suite work.

The report names the observed winning strategy even if it is the simple rule or default order. Results describe this scenario schedule and simulator only. Before claiming practical value, repeat the evaluation with permissioned real-lab history, genuinely future cycles and the actual operational cost model.

## Audit artifacts

Artifacts are saved under `runtime/experiment/` and excluded from source control by default.

| Artifact | Purpose |
|---|---|
| `dataset.jsonl` | Frozen feature vectors plus labels obtained after execution. |
| `ground_truth.json` | Predetermined scenario schedule and seeds, kept separate from features. |
| `execution_history.jsonl` | Full command/response evidence for sequential dataset generation. |
| `comparison_runs.jsonl` | Re-executed final suites for all three strategies. |
| `model/logistic-v1.json` | Versioned, inspectable model parameters. |
| `report.json` | Computed metrics, final-cycle comparisons and limitations. |
| `manifest.json` | Test-definition snapshot and SHA-256 hashes linking all artifacts. |

## Sources

- [scikit-learn: data leakage and fitting preprocessing on training data](https://scikit-learn.org/stable/common_pitfalls.html)
- [scikit-learn: chronological evaluation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)
- [scikit-learn: logistic regression](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LogisticRegression.html)
- [Spieker et al.: history-based test prioritization research](https://arxiv.org/abs/1811.04122)

The project adopts the engineering problem from the research. It does not claim to reproduce the RETECS reinforcement-learning method or its industrial results.
