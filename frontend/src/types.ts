export type RunState = 'queued' | 'running' | 'completed' | 'error' | 'interrupted'
export type TestStatus = 'pending' | 'running' | 'pass' | 'fail' | 'error'
export interface CatalogItem { id: string; label: string; description: string }
export interface Catalog {
  scenarios: CatalogItem[]
  strategies: CatalogItem[]
  tests: { id: string; name: string; description: string }[]
}
export interface TraceEntry { command: unknown; response: unknown; expected: unknown; ok: boolean }
export interface TestResult {
  id: string; name: string; status: TestStatus; duration_ms: number | null
  modeled_cost_ms: number; expected: unknown; actual: unknown; message: string; trace: TraceEntry[]
}
export interface RunSummary {
  id: string; created_at: string; finished_at: string | null; state: RunState
  scenario: string; strategy: string; seed: number; passed: number; failed: number
  errors: number; total: number; duration_ms: number | null; first_failure_ms: number | null; model_status: string
  suite_kind?: 'standard' | 'custom'; plan_name?: string | null
}
export interface RunDetail extends RunSummary {
  tests: TestResult[]; test_order: string[]; events: { at: string; level: string; message: string; test_id?: string }[]; error: string | null
  plan_snapshot?: PlanValidation | null
}
export interface ExperimentMethod {
  id: string; label: string; mean_first_failure_cost_ms: number | null; mean_total_cost_ms: number
  faulty_cycles: number; clean_cycles: number; failures_found: number; total_tests: number
}
export interface ExperimentCase {
  cycle: number; phase: string; scenario: string
  default_first_failure_cost_ms: number | null
  recent_first_first_failure_cost_ms: number | null
  ml_first_failure_cost_ms: number | null
}
export interface ExperimentReport {
  state: 'completed'; created_at: string; model_name: string; simulation_notice: string
  split: { train_cycles: number; validation_cycles: number; test_cycles: number }
  dataset: { cycles: number; rows: number; seed: number; features: string[] }
  methods: ExperimentMethod[]; cases: ExperimentCase[]; findings: string[]; limitations: string[]; training_seconds: number
}
export type Experiment = ExperimentReport | { state: 'not_trained' | 'running' } | { state: 'failed'; error: string }

export interface TestPlan {
  schema_version: '1.0'; name: string
  tests: { id: string; name: string; steps: { command: Record<string, unknown>; expected: Record<string, unknown> }[] }[]
}
export interface PlanValidation { plan: TestPlan; sha256: string; test_count: number; step_count: number }
export interface CampaignPlanSnapshot { plan: TestPlan | null; sha256: string; test_count: number; step_count: number; name?: string; kind?: string }
export type CampaignState = 'queued' | 'running' | 'pausing' | 'paused' | 'interrupted' | 'error' | 'completed'
export interface CampaignCase {
  id: string; scenario: string; seed: number; state: 'pending' | 'running' | 'completed' | 'interrupted' | 'error'
  run_id: string | null; attempts: string[]
}
export interface Campaign {
  id: string; name: string; state: CampaignState; created_at: string; finished_at: string | null
  plan_snapshot: CampaignPlanSnapshot | null; cases: CampaignCase[]; completed: number; total: number
  error: string | null; pause_requested: boolean; engine_fingerprint: string
}
export interface RunComparison {
  left: RunSummary; right: RunSummary; comparable: boolean; warnings: string[]
  conditions: { field: string; left: unknown; right: unknown; changed: boolean }[]
  summary: { common_tests: number; changed_verdicts: number; unchanged_verdicts: number; added_tests: number; removed_tests: number }
  added_test_ids: string[]; removed_test_ids: string[]
  changed_verdicts: { id: string; name: string; left_status: TestStatus; right_status: TestStatus }[]
  tests: { id: string; name: string; left: TestResult | null; right: TestResult | null; verdict_changed: boolean; definition_changed: boolean | null }[]
  timing: { wall_time_ms: ComparisonDelta; modeled_cost_ms: ComparisonDelta; first_failure_wall_time_ms: ComparisonDelta; note: string }
}
export interface ComparisonDelta { left: number | null; right: number | null; delta: number | null }

export interface CampaignComparisonSummary {
  id: string; name: string; state: CampaignState; created_at: string; finished_at: string | null
  total: number; completed: number; plan_name: string | null; plan_kind: string | null
  definition_fingerprint: string | null; engine_fingerprint: string | null
}
export interface CampaignComparisonSide {
  case_id: string; state: CampaignCase['state']; run_id: string | null
  attempts: string[]; attempt_count: number; run: RunSummary | null; warnings: string[]
}
export interface CampaignCaseDelta {
  compared_tests: number; new_failures: number; resolved_failures: number
  persistent_failures: number; unchanged_passes: number
  failed_left: number; failed_right: number; failed_delta: number
}
export interface CampaignComparisonCase {
  key: string; scenario: string; seed: number
  status: 'compared' | 'incomparable' | 'incomplete' | 'missing_run' | 'invalid_link' | 'added' | 'removed'
  comparable: boolean; warnings: string[]
  left: CampaignComparisonSide | null; right: CampaignComparisonSide | null
  delta: CampaignCaseDelta | null; comparison: RunComparison | null
}
export interface CampaignComparison {
  schema_version: '1.0'; note: string; left: CampaignComparisonSummary; right: CampaignComparisonSummary
  conditions: RunComparison['conditions']; warnings: string[]; comparable: boolean
  summary: {
    total_cases: number; matched_cases: number; comparable_cases: number; not_compared_cases: number
    added_cases: number; removed_cases: number; new_failure_cases: number; resolved_failure_cases: number
    changed_cases: number; unchanged_cases: number; compared_tests: number; new_failures: number
    resolved_failures: number; persistent_failures: number; unchanged_passes: number
  }
  cases: CampaignComparisonCase[]
}
