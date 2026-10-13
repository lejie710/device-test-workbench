import { useEffect, useRef, useState } from 'react'
import { AlertCircle, ArrowDownToLine, ArrowRight, Check, ChevronRight, Columns2, LoaderCircle } from 'lucide-react'
import { campaignApi } from './campaignApi'
import { Button } from './components/Button'
import type { Campaign, CampaignComparison, CampaignComparisonCase, CampaignComparisonSide, Catalog, TestResult } from './types'
import './campaign-comparison.css'

type CaseFilter = 'all' | 'changed' | 'not_compared'
const value = (data: unknown) => data === null || data === undefined ? 'Not recorded' : typeof data === 'string' ? data : JSON.stringify(data, null, 2)
const timestamp = (data: string) => new Date(data).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
const changed = (item: CampaignComparisonCase) => item.comparable && Boolean(item.delta && (item.delta.new_failures || item.delta.resolved_failures))
const filterCase = (item: CampaignComparisonCase, filter: CaseFilter) => filter === 'all' || (filter === 'changed' ? changed(item) : !item.comparable)
const statusLabels: Record<CampaignComparisonCase['status'], string> = {
  compared: 'Compared', incomparable: 'Conditions differ or are unverified', incomplete: 'Incomplete evidence',
  missing_run: 'Run evidence missing', invalid_link: 'Run link cannot be verified', added: 'Only in comparison', removed: 'Only in reference',
}

function Evidence({ result }: { result: TestResult | null }) {
  if (!result) return <p>This test has no saved observation on this side.</p>
  const decisive = result.trace.find(entry => !entry.ok) ?? result.trace.at(-1)
  return <>
    <span className={`status-label status-${result.status}`}>{result.status}</span>
    <div><label>Command</label><pre>{value(decisive?.command)}</pre></div>
    <div><label>Expected response</label><pre>{value(decisive ? decisive.expected : result.expected)}</pre></div>
    <div><label>Actual response</label><pre>{value(decisive ? decisive.response : result.actual)}</pre></div>
    <p>{result.message || 'No additional message was recorded.'}</p>
  </>
}

function Attempt({ side, label, onOpenRun }: { side: CampaignComparisonSide | null; label: string; onOpenRun: (id: string) => void }) {
  return <section className="campaign-compare-attempt" aria-label={`${label} case record`}>
    <div><h4>{label}</h4>{side?.run && <Button variant="ghost" size="small" onClick={() => onOpenRun(side.run!.id)}>Open run<ArrowRight aria-hidden="true" /></Button>}</div>
    {!side ? <p>No case with this scenario and seed.</p> : <>
      <p>Case state: <strong>{side.state}</strong></p>
      {side.attempt_count > 0 ? <p><strong>Recorded attempts: {side.attempt_count}.</strong> {Math.max(0, side.attempt_count - 1)} earlier {side.attempt_count === 2 ? 'attempt' : 'attempts'} retained.</p> : <p>No attempt recorded. This case has not produced evidence.</p>}
      {side.run ? <><p>Linked run state: <strong>{side.run.state}</strong></p><p>{side.run.passed} passed · {side.run.failed} failed · {side.run.errors} runner errors{side.run.state !== 'completed' && <span> (partial observations)</span>}</p><code>{side.run.id}</code></> : side.run_id ? <p>The linked run is unavailable; its absence is not a passing result.</p> : null}
      {side.warnings.length > 0 && <ul>{side.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
    </>}
  </section>
}

export default function CampaignComparisonView({ campaigns, catalog, connected, loading, onOpenRun }: {
  campaigns: Campaign[]; catalog: Catalog | null; connected: boolean; loading: boolean; onOpenRun: (id: string) => void
}) {
  const [left, setLeft] = useState('')
  const [right, setRight] = useState('')
  const [comparison, setComparison] = useState<CampaignComparison | null>(null)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<CaseFilter>('all')
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [testId, setTestId] = useState('')
  const [exported, setExported] = useState(false)
  const requestId = useRef(0)

  useEffect(() => {
    const nextLeft = campaigns.some(item => item.id === left) ? left : campaigns[1]?.id ?? campaigns[0]?.id ?? ''
    const nextRight = campaigns.some(item => item.id === right) ? right : campaigns[0]?.id ?? ''
    if (nextLeft !== left || nextRight !== right) {
      requestId.current++
      setLeft(nextLeft); setRight(nextRight)
      setComparison(null); setError(null); setWorking(false); setExported(false)
    }
  }, [campaigns, left, right])
  useEffect(() => () => { requestId.current++ }, [])

  function choose(side: 'left' | 'right', id: string) {
    requestId.current++
    ;(side === 'left' ? setLeft : setRight)(id)
    setComparison(null); setError(null); setWorking(false); setExported(false)
  }
  async function compare() {
    if (!left || !right || left === right) return
    const request = ++requestId.current
    setWorking(true); setError(null); setComparison(null); setSelectedKey(null); setExported(false)
    try {
      const report = await campaignApi.compareCampaigns(left, right)
      if (request !== requestId.current) return
      setComparison(report); setFilter('all'); setTestId('')
      const first = report.cases.find(changed) ?? report.cases.find(item => !item.comparable) ?? report.cases.find(item => item.delta?.persistent_failures) ?? report.cases[0]
      setSelectedKey(first?.key ?? null)
    } catch (reason) {
      if (request === requestId.current) setError(reason instanceof Error ? reason.message : 'The campaigns could not be compared. Try again.')
    } finally { if (request === requestId.current) setWorking(false) }
  }
  function exportSnapshot() {
    if (!comparison) return
    const blob = new Blob([JSON.stringify(comparison, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url; link.download = `campaign-comparison-${comparison.left.id.slice(0, 8)}-${comparison.right.id.slice(0, 8)}.json`
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    setExported(true)
  }
  function changeFilter(next: CaseFilter) {
    setFilter(next)
    if (!comparison?.cases.some(item => item.key === selectedKey && filterCase(item, next))) {
      setSelectedKey(comparison?.cases.find(item => filterCase(item, next))?.key ?? null); setTestId('')
    }
  }
  const scenarioLabel = (scenario: string) => catalog?.scenarios.find(item => item.id === scenario)?.label ?? scenario
  const campaignLabel = (item: Campaign) => `${item.name} · ${timestamp(item.created_at)} · ${item.id.slice(0, 8)}`
  const selected = comparison?.cases.find(item => item.key === selectedKey)
  const tests = selected?.comparison?.tests ?? []
  const selectedTest = tests.find(test => test.id === testId) ?? tests.find(test => test.verdict_changed || test.definition_changed) ?? tests.find(test => test.right?.status === 'fail' || test.left?.status === 'fail') ?? tests[0]
  const shown = comparison?.cases.filter(item => filterCase(item, filter)) ?? []
  const hasComparable = Boolean(comparison?.summary.comparable_cases)
  const sameCampaign = Boolean(left && left === right && campaigns.length >= 2)

  return <details className="campaign-comparison">
    <summary><span><Columns2 aria-hidden="true" />Compare campaigns</span><span>Match the case. Inspect the change.<ChevronRight aria-hidden="true" /></span></summary>
    <div className="compare-body">
      <p className="compare-intro">Compare two saved batches. A case is one device behavior and one seed; those conditions pair the cases across campaigns. Compare the latest attempt (the first run or a retry) on each side. Earlier attempts stay in campaign history.</p>
      <div className="compare-selectors">
        <label htmlFor="campaign-compare-left">Reference campaign<select id="campaign-compare-left" name="campaign-compare-left" value={left} onChange={event => choose('left', event.target.value)} disabled={!campaigns.length} aria-describedby={sameCampaign ? 'campaign-same-selection' : undefined}>{!campaigns.length && <option value="">No campaigns available</option>}{campaigns.map(item => <option key={item.id} value={item.id}>{campaignLabel(item)}</option>)}</select></label>
        <ArrowRight aria-hidden="true" />
        <label htmlFor="campaign-compare-right">Comparison campaign<select id="campaign-compare-right" name="campaign-compare-right" value={right} onChange={event => choose('right', event.target.value)} disabled={!campaigns.length} aria-describedby={sameCampaign ? 'campaign-same-selection' : undefined}>{!campaigns.length && <option value="">No campaigns available</option>}{campaigns.map(item => <option key={item.id} value={item.id}>{campaignLabel(item)}</option>)}</select></label>
        <Button variant="secondary" onClick={() => void compare()} disabled={!connected || !left || !right || left === right || working}>{working ? <LoaderCircle className="spin" aria-hidden="true" /> : <Columns2 aria-hidden="true" />}{working ? 'Comparing…' : 'Compare campaigns'}</Button>
      </div>
      <div aria-live="polite" className="campaign-compare-feedback">
        {loading && !campaigns.length ? <p>Loading saved campaigns…</p> : campaigns.length < 2 ? <p>Save at least two campaigns to compare them. Start another campaign above with the same scenarios, seeds and definition.</p> : sameCampaign ? <p className="field-error" id="campaign-same-selection">Choose two different campaigns. One saved campaign cannot provide two independent observations.</p> : null}
        {!connected && <p>The local API is unavailable. Reconnect to load a comparison.</p>}
        {working && <p>Reading saved cases and checking their conditions…</p>}
      </div>
      {error && <div className="inline-warning" role="alert"><AlertCircle aria-hidden="true" /><span>{error} Choose the campaigns and try Compare campaigns again.</span></div>}
      {comparison && <div className="campaign-comparison-result">
        <div className={`comparability-note ${comparison.comparable ? 'comparable' : ''}`}>
          {comparison.comparable ? <Check aria-hidden="true" /> : <AlertCircle aria-hidden="true" />}
          <div><strong>{comparison.comparable ? 'Matching cases and complete evidence' : hasComparable ? 'Some cases are excluded from failure changes' : 'No cases qualify for failure-change totals'}</strong><p>{comparison.comparable ? 'Saved conditions match for every paired case. Observed differences alone do not establish cause.' : 'Read the exclusions before interpreting the totals. Missing work, different definitions and runner errors are not passing device observations.'}</p></div>
        </div>
        {comparison.warnings.length > 0 && <ul className="comparison-warnings">{comparison.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
        <details className="condition-details"><summary>Inspect campaign conditions<ChevronRight aria-hidden="true" /></summary><div className="comparison-condition-table" tabIndex={0} role="region" aria-label="Campaign condition comparison"><table><thead><tr><th scope="col">Condition</th><th scope="col">Reference</th><th scope="col">Comparison</th></tr></thead><tbody>{comparison.conditions.map(item => <tr key={item.field} className={item.changed ? 'condition-changed' : ''}><th scope="row">{item.field.replaceAll('_', ' ')}{item.changed && <span className="sr-only"> (different)</span>}</th><td><code>{value(item.left)}</code></td><td><code>{value(item.right)}</code></td></tr>)}</tbody></table></div></details>
        <div className="campaign-compare-overview"><div className="campaign-compare-summary" aria-label="Campaign comparison summary">
          <div><strong>{comparison.summary.comparable_cases}<span> / {comparison.summary.total_cases}</span></strong><span>Comparable cases</span></div>
          <div><strong>{hasComparable ? comparison.summary.changed_cases : '—'}</strong><span>Cases with changed verdicts</span></div>
          <div><strong>{comparison.summary.not_compared_cases}</strong><span>Not compared</span></div>
        </div>
        <p className="campaign-compare-count-help">Comparable cases have complete latest runs and matching conditions. Only their paired tests enter the pass/fail totals. Not compared cases stay visible with a reason and contribute no passing or failing observations.</p><p className="campaign-compare-scope">{comparison.summary.matched_cases} matched by scenario and seed · {comparison.summary.added_cases} only in comparison · {comparison.summary.removed_cases} only in reference</p>
        <div className="sr-only" role="status">{comparison.summary.comparable_cases} of {comparison.summary.total_cases} cases comparable. {comparison.summary.not_compared_cases} cases not compared.</div>
        {hasComparable ? <div className="campaign-compare-deltas"><p>Across {comparison.summary.comparable_cases} comparable cases and {comparison.summary.compared_tests} paired test observations:</p><div><span><strong>{comparison.summary.new_failures}</strong> pass to fail</span><span><strong>{comparison.summary.resolved_failures}</strong> fail to pass</span><span><strong>{comparison.summary.persistent_failures}</strong> fail on both</span><span><strong>{comparison.summary.unchanged_passes}</strong> pass on both</span></div></div> : <p className="campaign-compare-unavailable">Failure changes are unavailable. No complete, compatible case pairs were compared.</p>}</div>
        <div className="campaign-compare-cases-heading"><h3>Scenario and seed pairs</h3><label htmlFor="campaign-case-filter">Show<select id="campaign-case-filter" name="campaign-case-filter" value={filter} onChange={event => changeFilter(event.target.value as CaseFilter)}><option value="all">All cases ({comparison.summary.total_cases})</option><option value="changed">Changed verdicts ({comparison.summary.changed_cases})</option><option value="not_compared">Not compared ({comparison.summary.not_compared_cases})</option></select></label></div>
        <div className="campaign-compare-cases" aria-label="Campaign case comparisons">
          {shown.length === 0 ? <p className="no-differences">{filter === 'changed' ? hasComparable ? 'No changed verdicts within the comparable case set.' : 'Changed verdicts are unavailable because no case pairs are comparable.' : 'No cases match this filter.'}</p> : shown.map(item => <button type="button" key={item.key} className={selectedKey === item.key ? 'selected' : ''} onClick={() => { setSelectedKey(item.key); setTestId('') }} aria-pressed={selectedKey === item.key} aria-controls="campaign-case-evidence"><span><strong>{scenarioLabel(item.scenario)}</strong><span>Seed {item.seed}</span></span><span className={`campaign-case-outcome ${item.comparable ? '' : 'excluded'}`}><strong>{item.comparable && item.delta ? changed(item) ? `${item.delta.new_failures} pass to fail · ${item.delta.resolved_failures} fail to pass` : 'Verdicts unchanged' : statusLabels[item.status]}</strong><span>{item.comparable && item.delta ? `${item.delta.failed_left} → ${item.delta.failed_right} failed of ${item.delta.compared_tests} tests` : 'Excluded from failure changes'}</span></span><ChevronRight aria-hidden="true" /></button>)}
        </div>
        {selected && <section className="campaign-case-evidence" id="campaign-case-evidence" aria-labelledby="campaign-case-evidence-heading">
          <div className="campaign-compare-evidence-heading"><div><h3 id="campaign-case-evidence-heading">{scenarioLabel(selected.scenario)} <span>Seed {selected.seed}</span></h3><p>{selected.comparable ? 'Matched case. The latest linked attempt on each side is shown below.' : `${statusLabels[selected.status]}. Inspect the record without including it in failure-change totals.`}</p></div><span className={`campaign-case-badge ${selected.comparable ? 'is-comparable' : ''}`}>{selected.comparable ? 'Comparable' : 'Not compared'}</span></div>
          {selected.warnings.length > 0 && <ul className="comparison-warnings">{selected.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
          <div className="campaign-compare-attempts"><Attempt side={selected.left} label="Reference" onOpenRun={onOpenRun} /><Attempt side={selected.right} label="Comparison" onOpenRun={onOpenRun} /></div>
          {selected.comparison && <>
            <details className="condition-details"><summary>Inspect case conditions<ChevronRight aria-hidden="true" /></summary><div className="comparison-condition-table" tabIndex={0} role="region" aria-label="Selected case condition comparison"><table><thead><tr><th scope="col">Condition</th><th scope="col">Reference</th><th scope="col">Comparison</th></tr></thead><tbody>{selected.comparison.conditions.map(item => <tr key={item.field} className={item.changed ? 'condition-changed' : ''}><th scope="row">{item.field.replaceAll('_', ' ')}{item.changed && <span className="sr-only"> (different)</span>}</th><td><code>{value(item.left)}</code></td><td><code>{value(item.right)}</code></td></tr>)}</tbody></table></div></details>
            <label className="campaign-compare-test-picker" htmlFor="campaign-compare-test">Inspect a test<select id="campaign-compare-test" name="campaign-compare-test" value={selectedTest?.id ?? ''} onChange={event => setTestId(event.target.value)}>{tests.map(test => <option key={test.id} value={test.id}>{test.name} · {test.left?.status ?? 'absent'} to {test.right?.status ?? 'absent'}{test.definition_changed ? ' · definition differs' : ''}</option>)}</select></label>
            {selectedTest && <div className="paired-evidence"><div className="paired-evidence-heading"><h4>{selectedTest.name}</h4><span>Decisive saved evidence</span></div><p className="paired-evidence-context">Each side shows its first mismatched command, or its last recorded command when none mismatched. Full trace opens all commands in that saved run.{selectedTest.definition_changed ? ' This test definition differs between campaigns.' : ''}</p>{(['left', 'right'] as const).map(side => <section key={side}><div className="paired-evidence-label"><span>{side === 'left' ? 'Reference' : 'Comparison'}</span><Button variant="ghost" size="small" onClick={() => onOpenRun(selected.comparison![side].id)}>Full trace<ArrowRight aria-hidden="true" /></Button></div><Evidence result={selectedTest[side]} /></section>)}</div>}
          </>}
        </section>}
        <div className="campaign-compare-export"><p>This view is a saved snapshot. Compare again after a campaign progresses. Export preserves the evidence currently shown.</p><Button variant="ghost" size="small" onClick={exportSnapshot}><ArrowDownToLine aria-hidden="true" />Export comparison JSON</Button></div>
        <p className="sr-only" role="status">{exported ? 'The displayed campaign comparison was exported as JSON.' : ''}</p>
      </div>}
    </div>
  </details>
}
