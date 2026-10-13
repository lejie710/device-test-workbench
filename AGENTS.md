# Device Test Lab agent instructions

Read [README.md](README.md), [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/VERIFICATION.md](docs/VERIFICATION.md) when starting work. Read [docs/ENGINEERING_NOTES.md](docs/ENGINEERING_NOTES.md) for product boundaries and [docs/API_CONTRACT.md](docs/API_CONTRACT.md) before changing backend, frontend or ML integration.

## Preserve evidence

Keep device assertion failures separate from runner errors and interruption. A completed run executes every test in its saved plan, including after an earlier failure. Preserve independent expectations, actual responses, command traces, immutable definition snapshots and prior attempts. Missing or pending observations never become PASS.

Campaigns and standalone runs share one worker. Pause takes effect after the current complete run. Resumption is explicit, preserves completed cases and creates new retry attempts from fresh device state. Keep the documented case, attempt and plan limits, atomic run/attempt linking, runtime ownership lock and conservative compatibility checks.

Before changing comparison behavior, read [docs/RUN_COMPARISON.md](docs/RUN_COMPARISON.md) and [docs/CAMPAIGN_COMPARISON.md](docs/CAMPAIGN_COMPARISON.md). Comparison reports changed conditions and observations; it does not infer a root cause or apply a universal regression threshold. Preserve old records and expose missing provenance rather than filling it with current values.

## Keep evaluation honest

For ordering changes, read [docs/ML_EXPERIMENT.md](docs/ML_EXPERIMENT.md) and [docs/ML_ROBUSTNESS_PROTOCOL.md](docs/ML_ROBUSTNESS_PROTOCOL.md). Features use only information available before each run. Fit preprocessing and models on the training cohort, select only on validation and keep final evaluation separate. Use actual executed assertions as labels. Compare complete suites with equivalent conditions and history, and retain negative results when a simpler method wins.

Keep modeled cost separate from measured wall time. Synthetic results do not support physical-device or production-performance claims. Custom plans remain outside the standard catalog model unless their compatibility is explicitly implemented and verified.

## Verify the application

Follow the existing composition in [docs/design/README.md](docs/design/README.md). Review the rendered application for relevant empty, active, failed and unavailable states, keyboard behavior, light/dark appearance and narrow widths. Render actual API values and model fallback status.

Use isolated runtimes and owned process cleanup for checks. Back up an existing runtime consistently before a migration. Run the applicable checks in CONTRIBUTING.md, review the final diff and report actual results and limitations. Keep setup, contracts and verification instructions aligned with changes.

Use original synthetic fixtures. Keep credentials, personal records, real lab artifacts and generated runtime data outside tracked changes. Code, comments, interface text, documentation and sample data are English-only.
