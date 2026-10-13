# Integration contract

This contract describes the backend, frontend and ML interfaces. Update callers and their tests together when changing an interface. Backend package is devicetestlab under backend/. Frontend is React/TypeScript under frontend/. Local runtime artifacts live under runtime/ and are ignored by Git. All dates emitted by the API use ISO 8601 UTC.

## Backend API

- GET /api/health -> {status: "ok", version: "0.3.1"}.
- GET /api/catalog -> {scenarios: [{id,label,description}], tests: [{id,name,description}], strategies: [{id,label,description}]}.
- GET /api/runs -> {runs: RunSummary[]} newest first, bounded to 100.
- POST /api/runs body {scenario, strategy, seed} -> RunDetail. Return 202 promptly with state queued/running; execute on a single bounded background worker. Allow only one active run and return HTTP 409 if busy.
- GET /api/runs/{id} -> RunDetail, 404 if absent.
- GET /api/runs/{id}/export -> a downloadable JSON copy of the run and evidence.
- GET /api/experiment -> the last saved ML experiment report, or {state:"not_trained"}.
- POST /api/experiment -> 202 {state:"running"}, use the same worker/busy check. Run the bounded experiment and save its report; GET returns {state:"running"} during execution, or {state:"failed",error} on failure.

Scenario IDs: healthy, corrupt_read, ignored_write, stuck_state, timeout. Strategy IDs: default, recent_first, ml. Unknown scenarios/strategies are rejected with useful validation errors. Seed is an integer in 0..2147483647.

RunSummary: {id,created_at,finished_at,state,scenario,strategy,seed,passed,failed,errors,total,duration_ms,first_failure_ms,model_status}. State is queued|running|completed|error|interrupted. first_failure_ms is null for no failures, or until a failure is observed. model_status explains actual model use or explicit fallback.

RunDetail extends RunSummary with {tests: TestResult[],test_order: string[],events: Event[],error: string|null}. TestResult: {id,name,status,duration_ms,modeled_cost_ms,expected,actual,message,trace}. status is pending|running|pass|fail|error. Trace is an array of {command,response,expected,ok}; values must be JSON-compatible. Event: {at,level,message,test_id?}. Preserve immutable test definition/version metadata in storage even if not all are shown in summary.

## Device runner interface shared with ML

backend/devicetestlab/engine.py exports:

- TESTS: list of dictionaries with id, name, description, modeled_cost_ms.
- SCENARIOS: list of dictionaries with id, label, description.
- execute_test(test_id: str, scenario: str, seed: int) -> TestResult dictionary. Each call uses a fresh device and deterministic scenario/seed; no deliberate sleep is needed for modeled timing. Include actual elapsed duration_ms and explicit modeled_cost_ms separately.
- run_suite(scenario: str, seed: int, order: list[str] | None = None, on_result: callable | None = None) -> list[TestResult]. Execute the entire requested full suite and callback after each result. Invalid/incomplete/duplicate orders raise a useful error.

The ordering pipeline imports these functions. Engine tests and simulator assertions establish observed labels; ML must not create current-run PASS/FAIL labels directly.

## ML service interface

backend/devicetestlab/ml_service.py exports:

- prioritize(strategy: str, history: list[dict], model_dir: pathlib.Path) -> tuple[list[str],str]. history consists of chronological completed RunDetail dictionaries, each with tests. Use TESTS for every ID. Return every test exactly once and a readable actual-use/fallback status. If ml has no usable saved model, fall back explicitly.
- run_experiment(output_dir: pathlib.Path) -> dict. Write a versioned model artifact under output_dir/model/, a transparent dataset/manifest and experiment report. Return report JSON. CPU threads are bounded to one.

Experiment JSON must include state:"completed", created_at, model_name, simulation_notice, split {train_cycles,validation_cycles,test_cycles}, dataset {cycles,rows,seed,features}, methods [{id,label,mean_first_failure_cost_ms,mean_total_cost_ms,faulty_cycles,clean_cycles,failures_found,total_tests}], cases [{cycle,phase,scenario,default_first_failure_cost_ms,recent_first_first_failure_cost_ms,ml_first_failure_cost_ms}], findings:string[], limitations:string[], training_seconds. All numerical results are computed, never chosen for display. Null denotes no failure. Additional fields are welcome if useful. Per-method wall-time figures may be added but must be separated from modeled cost and include the measurement conditions.

API model_dir and experiment output_dir refer to runtime/experiment and its model subfolder consistently. Never accept arbitrary model upload or untrusted serialized model paths from the browser.

## Frontend

Use relative /api URLs. Development proxy targets http://127.0.0.1:8717 by default; allow DTL_API_TARGET override. Vite uses an explicit loopback host and a separate free port. Production can be served by FastAPI from frontend/dist. Treat missing/failed backend, empty history, queued/running results, failure evidence, training progress and training errors as distinct interface states. Poll only during active work, with bounded backoff for errors.

Use the Studio interface conventions in docs/design/README.md. The app has four top-level destinations: Test bench, Run history, Campaigns, Ordering lab. Run history includes saved-run comparison. Avoid new accounts, cloud hosting, or a settings system. Display model fallback and the simulation boundary visibly where they affect interpretation. The interface must render real API values, including losses to the simple rule.

## Declarative plans and campaigns (0.2.0)

- POST /api/plans/validate accepts a raw plan and returns its normalized snapshot: {kind:"custom",plan,name,sha256,test_count,step_count}.
- A plan is {schema_version:"1.0",name,tests:[{id,name,steps:[{command,expected}]}]}. Supported operations are READ, WRITE, RESET and STATUS. WRITE requires address and value; READ requires address. RESET and STATUS accept no numeric arguments. Unknown fields are rejected. Numbers are strict integers, never booleans. Command addresses -1 through 16 and values -1 through 256 permit intentional boundary-error assertions; the simulated device's valid memory remains addresses 0-15 and byte values 0-255.
- Names must contain visible text. Test IDs use lowercase letters, numbers and underscores, beginning with a letter. Limits are 32 tests, 64 steps per test, 256 total steps and 96,000 UTF-8 bytes in the normalized model serialization. Exact expected response objects are asserted independently of device behavior.
- POST /api/runs additionally accepts optional plan. A custom plan requires strategy default, runs in declared order and is excluded from standard-model history. RunDetail adds suite_kind, plan_name, plan_snapshot, engine_fingerprint and, for campaign attempts, campaign_id, campaign_case_id and attempt_number. Older V1 records remain readable.
- POST /api/campaigns body {name,scenarios:[Scenario],seeds:[integer],plan:null|TestPlan} returns 202 with the queued campaign snapshot. Scenario and seed selections must each be unique; their Cartesian product is limited to 25 cases.
- GET /api/campaigns returns {campaigns:Campaign[]} newest first, at most 100. GET /api/campaigns/{id} returns one campaign.
- POST /api/campaigns/{id}/pause requests stopping after the current full run. POST /api/campaigns/{id}/resume returns 202 after compatibility and shared-worker checks. Only paused, interrupted or errored campaigns can resume. Completed cases and earlier attempts are retained. A retry creates a new run ID from fresh device state; nothing resumes automatically on startup.
- GET /api/campaigns/{id}/export downloads {campaign,runs}, including every recorded attempt and its evidence.

Campaign: {id,name,state,created_at,finished_at,plan_snapshot,engine_fingerprint,cases,completed,total,error,pause_requested}. State is queued|running|pausing|paused|interrupted|error|completed. Each case is {id,scenario,seed,state,run_id,attempts:[runId]}; run_id identifies the latest attempt. Completed means its full plan executed without runner errors, and may include failed device assertions. Plan definitions, case conditions and completed records are immutable. A run and its campaign attempt link are saved in one transaction. A campaign allows at most 50 attempts including retries, enforced before every new attempt. Resume rejects any different engine source fingerprint and checks standard-definition compatibility; this conservative byte-level check does not infer semantic equivalence. The campaign shares the same single worker and busy gate as individual runs and model fitting.

## Saved-run comparison (0.2.0)

GET /api/compare?left=runId&right=runId returns both summaries, comparable:boolean, warnings, conditions:[{field,left,right,changed}], summary counts, added_test_ids, removed_test_ids, changed_verdicts, tests and timing. Each test row retains the full left/right observations plus verdict_changed and definition_changed. Unknown runs return 404.

Comparability considers scenario, seed, definition fingerprint, engine context, test membership and completeness. Different ordering is explained separately because every complete run retains the full suite. A warning is an interpretation aid, not a causal diagnosis or universal regression threshold. Timing deltas are right minus left; laptop wall time and modeled cost are separate, with null for unavailable values. A missing or pending result is never converted into a passing observation.

## Saved-campaign comparison (0.3.0)

- GET /api/campaign-compare?left=campaignId&right=campaignId returns a read-only comparison. GET /api/campaign-compare/export with the same query downloads the same report shape as JSON. Unknown campaign IDs return 404; either missing query parameter returns 422. Comparison is available while the shared worker is busy and never queues work.
- The report is {schema_version:"1.0",left,right,conditions,warnings,comparable,summary,cases,note}. Campaign summaries contain {id,name,state,created_at,finished_at,total,completed,plan_name,plan_kind,definition_fingerprint,engine_fingerprint}. Conditions use the existing {field,left,right,changed} shape for saved plan kind/name, definition and engine fingerprints, scenarios and seeds.
- Cases align by exact (scenario,seed), preserving left-case order and appending right-only conditions. Case IDs and array positions are not cross-campaign identities. Only case.run_id is selected. The latest pointer must match the final recorded attempt and its saved run must match the owning campaign, case, conditions, attempt number, full test membership and saved definition context. Missing or stale latest attempts never fall back to an earlier run.
- A case row is {key,scenario,seed,status,comparable,warnings,left,right,delta,comparison}. key is "scenario:seed". Status is compared|incomparable|incomplete|missing_run|invalid_link|added|removed. A missing case side is null; an existing side is {case_id,state,run_id,attempts,attempt_count,run,warnings}. run is a saved run summary or null, without invented metadata. comparison is the complete saved-run comparison including observations and traces when both selected runs exist, otherwise null. The nested comparable flag also respects the campaign integrity checks.
- delta is null for every case outside the comparable denominator. Otherwise it is {compared_tests,new_failures,resolved_failures,persistent_failures,unchanged_passes,failed_left,failed_right,failed_delta}. Its counts come from recorded test statuses, not campaign totals. New failures are PASS to FAIL, resolved failures are FAIL to PASS, persistent failures are FAIL to FAIL, and unchanged passes are PASS to PASS. failed_delta is right minus left.
- Summary contains {total_cases,matched_cases,comparable_cases,not_compared_cases,added_cases,removed_cases,new_failure_cases,resolved_failure_cases,changed_cases,unchanged_cases,compared_tests,new_failures,resolved_failures,persistent_failures,unchanged_passes}. total_cases is the union of distinct scenario/seed conditions. Duplicate cases make that condition invalid instead of selecting an arbitrary comparable pair. not_compared_cases includes added and removed conditions. changed_cases counts cases with a PASS/FAIL transition; new_failure_cases and resolved_failure_cases may overlap when different tests move in opposite directions. unchanged_cases means unchanged PASS/FAIL verdicts, not identical wall time or traces.
- Only independent latest attempts with matching saved campaign/run conditions and complete PASS/FAIL suites contribute to counts. Missing definition or engine provenance is unverified; nothing is filled from the current engine. Incomplete campaigns can contain comparable completed cases. Pending work, runner errors, interruptions, missing runs and changed definitions stay visible outside the denominator. Same-campaign selection warns that there are no independent observations and contributes no counts. Top-level comparable is true only when at least one aligned case exists and every row is compared.
- Comparison creates independent returned data and does not change any saved record. Each referenced latest run is loaded at most once per request. Active campaigns may advance after a report is read; the UI exports the exact displayed response, while the API export endpoint reads a fresh report. Completed evidence stays immutable. The report preserves the underlying expected/actual evidence and states the simulation and causal limits; no regression threshold or causal verdict is inferred.

The [campaign comparison guide](CAMPAIGN_COMPARISON.md) explains the denominator and interpretation with a worked example.
