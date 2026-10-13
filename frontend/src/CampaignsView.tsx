import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertCircle, ArrowDownToLine, ArrowRight, Check, ChevronRight, FileJson, Fingerprint, Layers3, LoaderCircle, Pause, Play, RefreshCw, Upload } from 'lucide-react'
import { campaignApi } from './campaignApi'
import { Button } from './components/Button'
import CampaignComparisonView from './CampaignComparisonView'
import type { Campaign, Catalog, PlanValidation, TestPlan } from './types'

const examplePlan: TestPlan = {
  schema_version: '1.0',
  name: 'Byte round trip and reset',
  tests: [
    { id: 'byte_roundtrip', name: 'Store a byte and clear it on reset', steps: [
      { command: { op: 'WRITE', address: 3, value: 165 }, expected: { ok: true } },
      { command: { op: 'READ', address: 3 }, expected: { ok: true, value: 165 } },
      { command: { op: 'RESET' }, expected: { ok: true } },
      { command: { op: 'READ', address: 3 }, expected: { ok: true, value: 0 } },
    ] },
  ],
}

const isActive = (campaign: Campaign) => ['queued', 'running', 'pausing'].includes(campaign.state)
const readableError = (value: unknown) => value instanceof Error ? value.message : 'The action did not complete. Please try again.'
const timestamp = (value: string) => new Date(value).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
const stateText = (campaign: Campaign) => ({ queued: 'Queued', running: 'Running', pausing: 'Pausing after current run', paused: 'Paused', interrupted: 'Interrupted', error: 'Execution issue', completed: 'Complete' })[campaign.state]

interface Props {
  catalog: Catalog | null
  busy: boolean
  connected: boolean
  visible: boolean
  onOpenRun: (id: string) => void
  onBusyChange: (busy: boolean) => void
  onRunsChanged: () => void
}

export default function CampaignsView({ catalog, busy, connected, visible, onOpenRun, onBusyChange, onRunsChanged }: Props) {
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [selected, setSelected] = useState<Campaign | null>(null)
  const [name, setName] = useState('Device integrity sweep')
  const [scenarios, setScenarios] = useState(['healthy', 'ignored_write'])
  const [seeds, setSeeds] = useState('3, 4')
  const [custom, setCustom] = useState(false)
  const [planText, setPlanText] = useState(JSON.stringify(examplePlan, null, 2))
  const [validation, setValidation] = useState<PlanValidation | null>(null)
  const [validating, setValidating] = useState(false)
  const [working, setWorking] = useState(false)
  const [loading, setLoading] = useState(false)
  const [inspecting, setInspecting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [planError, setPlanError] = useState<string | null>(null)
  const [pollAttempt, setPollAttempt] = useState(0)
  const fileInput = useRef<HTMLInputElement>(null)
  const selectedId = useRef<string | null>(null)
  const selectionRequest = useRef(0)
  const reloadRequest = useRef(0)
  const hasLoaded = useRef(false)
  const callbacks = useRef({ onBusyChange, onRunsChanged })
  callbacks.current = { onBusyChange, onRunsChanged }

  const updateList = useCallback((items: Campaign[]) => {
    setCampaigns(items)
  }, [])
  useEffect(() => { callbacks.current.onBusyChange(working || campaigns.some(isActive)) }, [working, campaigns])

  const reload = useCallback(async () => {
    const request = ++reloadRequest.current
    setLoading(true)
    try {
      const { campaigns: next } = await campaignApi.list()
      if (request !== reloadRequest.current) return
      updateList(next)
      const id = selectedId.current ?? next[0]?.id
      if (id) {
        selectedId.current = id
        const selection = selectionRequest.current
        const detail = await campaignApi.get(id)
        if (request !== reloadRequest.current || selection !== selectionRequest.current) return
        setSelected(detail)
      }
      setError(null)
      setPollAttempt(value => value + 1)
    } catch (reason) { if (request === reloadRequest.current) setError(readableError(reason)) }
    finally { if (request === reloadRequest.current) setLoading(false) }
  }, [updateList])

  useEffect(() => {
    if (!connected) { hasLoaded.current = false; return }
    if (visible || !hasLoaded.current) { hasLoaded.current = true; void reload() }
  }, [visible, connected, reload])
  const activeId = campaigns.find(isActive)?.id
  useEffect(() => {
    if (!activeId) return
    let stopped = false, timer = 0, failures = 0
    const poll = async () => {
      try {
        const detail = await campaignApi.get(activeId)
        if (stopped) return
        failures = 0
        if (selectedId.current === detail.id) setSelected(detail)
        setCampaigns(current => current.map(item => item.id === detail.id ? detail : item))
        if (isActive(detail)) timer = window.setTimeout(poll, 650)
        else { callbacks.current.onRunsChanged(); const { campaigns: next } = await campaignApi.list(); if (!stopped) updateList(next) }
      } catch (reason) {
        if (stopped) return
        failures++
        if (failures < 3) timer = window.setTimeout(poll, failures * 1500)
        else setError(`${readableError(reason)} Campaign refresh paused. Use Refresh to reconnect.`)
      }
    }
    timer = window.setTimeout(poll, 350)
    return () => { stopped = true; window.clearTimeout(timer) }
  }, [activeId, pollAttempt, updateList])

  function parseSeeds(): number[] {
    const values = seeds.split(/[,\s]+/).filter(Boolean)
    if (!values.length || values.some(value => !/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)) || Number(value) > 2147483647)) throw new Error('Enter comma-separated whole-number seeds from 0 to 2147483647.')
    const parsed = values.map(Number)
    if (new Set(parsed).size !== parsed.length) throw new Error('Each seed must be unique. Remove repeated seeds before starting.')
    return parsed
  }
  let count = 0, seedsError: string | null = null
  try { count = scenarios.length * parseSeeds().length } catch (reason) { seedsError = readableError(reason) }
  const scenariosError = !scenarios.length ? 'Choose at least one scenario.' : null
  const countError = count > 25 ? 'A campaign can contain at most 25 scenario-and-seed cases. Remove scenarios or seeds.' : null

  async function validatePlan() {
    setValidating(true); setPlanError(null); setValidation(null)
    try {
      let parsed: unknown
      try { parsed = JSON.parse(planText) } catch { throw new Error('This is not valid JSON. Check commas, quotes and brackets, then validate again.') }
      setValidation(await campaignApi.validate(parsed))
    } catch (reason) { setPlanError(readableError(reason)) }
    finally { setValidating(false) }
  }

  async function importFile(file?: File) {
    if (!file) return
    setPlanError(null); setValidation(null)
    try {
      if (file.size > 128 * 1024) throw new Error('Choose a JSON file no larger than 128 KB.')
      setPlanText(await file.text())
    } catch (reason) { setPlanError(readableError(reason)) }
    if (fileInput.current) fileInput.current.value = ''
  }

  async function create(event: React.FormEvent) {
    event.preventDefault(); setError(null)
    if (scenariosError) { document.querySelector<HTMLInputElement>('.scenario-choices input')?.focus(); return }
    if (seedsError || countError) { document.getElementById('campaign-seeds')?.focus(); return }
    try {
      if (!name.trim()) throw new Error('Give this campaign a name.')
      const parsed = parseSeeds()
      if (custom && !validation) throw new Error('Validate the current JSON plan before starting a custom campaign.')
      setWorking(true)
      const next = await campaignApi.create(name.trim(), scenarios, parsed, custom ? validation!.plan : null)
      selectionRequest.current++
      reloadRequest.current++
      setLoading(false)
      setInspecting(false)
      selectedId.current = next.id; setSelected(next)
      updateList([next, ...campaigns.filter(item => item.id !== next.id)])
      if (!isActive(next)) callbacks.current.onRunsChanged()
    } catch (reason) { setError(readableError(reason)) }
    finally { setWorking(false) }
  }

  async function control(action: 'pause' | 'resume') {
    if (!selected) return
    const id = selected.id
    setWorking(true); setError(null)
    try {
      const next = await campaignApi[action](id)
      if (selectedId.current === id) setSelected(next)
      updateList(campaigns.map(item => item.id === next.id ? next : item)); setPollAttempt(value => value + 1)
      if (!isActive(next)) callbacks.current.onRunsChanged()
    } catch (reason) { setError(readableError(reason)) }
    finally { setWorking(false) }
  }

  async function inspect(id: string) {
    const request = ++selectionRequest.current
    selectedId.current = id
    setError(null); setInspecting(true)
    try { const detail = await campaignApi.get(id); if (request === selectionRequest.current) setSelected(detail) }
    catch (reason) { if (request === selectionRequest.current) setError(readableError(reason)) }
    finally { if (request === selectionRequest.current) setInspecting(false) }
  }

  return <section className="page-section campaigns-view">
    <div className="page-heading"><div><div className="eyebrow">REPEATABLE VALIDATION CAMPAIGNS</div><h1>Run a batch.<br />Keep every <em>result.</em></h1><p className="subtitle">A campaign runs one saved test plan across several device behaviors and seeds.</p></div><div className="page-symbol"><Layers3 /></div></div>
    {error && <div className="inline-warning" role="alert"><AlertCircle /><span>{error}</span></div>}
    <form className="campaign-builder" onSubmit={event => void create(event)}>
      <div className="campaign-section-heading"><div><h2>Build a campaign</h2><p>A case is one device behavior and one seed. Each case runs the whole plan.</p></div><span className={`case-count ${count > 25 ? 'status-error' : ''}`}>{count} / 25 cases</span></div>
      <div className="campaign-form-grid"><label htmlFor="campaign-name">Campaign name<input id="campaign-name" name="campaign-name" autoComplete="off" value={name} onChange={event => setName(event.target.value)} maxLength={80} required /></label><label htmlFor="campaign-seeds">Seeds<input id="campaign-seeds" name="campaign-seeds" autoComplete="off" value={seeds} onChange={event => setSeeds(event.target.value)} placeholder="3, 4…" inputMode="text" aria-invalid={Boolean(seedsError || countError)} aria-describedby={`seeds-help${seedsError ? " seeds-error" : ""}${countError ? " cases-error" : ""}`} /><span id="seeds-help">Comma-separated whole numbers. For Corrupted read and Ignored write, seed picks the fault address. Healthy device ignores it.</span>{seedsError && <span className="field-error" id="seeds-error" aria-live="polite">{seedsError}</span>}{countError && <span className="field-error" id="cases-error" aria-live="polite">{countError}</span>}</label></div>
      <fieldset className="scenario-choices" aria-invalid={Boolean(scenariosError)} aria-describedby={scenariosError ? "scenarios-error" : undefined}><legend>Device scenarios (behaviors)</legend><div>{catalog?.scenarios.map(item => <label className={scenarios.includes(item.id) ? 'chosen' : ''} key={item.id}><input type="checkbox" checked={scenarios.includes(item.id)} onChange={event => setScenarios(current => event.target.checked ? [...current, item.id] : current.filter(id => id !== item.id))} /><span>{item.label}</span></label>)}</div>{scenariosError && <p className="field-error" id="scenarios-error" aria-live="polite">{scenariosError}</p>}</fieldset>
      <div className="plan-choice"><div><span className="field-label">Test definition</span><div className="filter-group" role="group" aria-label="Choose test definition"><button type="button" aria-pressed={!custom} className={!custom ? 'selected' : ''} onClick={() => setCustom(false)}>Standard catalog</button><button type="button" aria-pressed={custom} className={custom ? 'selected' : ''} onClick={() => setCustom(true)}>Custom JSON plan</button></div></div><p>{custom ? 'Custom plans execute in their declared order. The standard ML model is not compatible.' : `${catalog?.tests.length ?? 'All'} catalog tests run in their documented order.`}</p></div>
      {custom && <div className="plan-workspace"><div className="plan-editor-pane"><div className="plan-editor-heading"><label htmlFor="plan-json"><FileJson />Plan JSON</label><div><input type="file" accept=".json,application/json" ref={fileInput} className="sr-only" aria-label="Import JSON plan file" onChange={event => void importFile(event.target.files?.[0])} /><Button variant="ghost" size="small" onClick={() => fileInput.current?.click()} disabled={validating}><Upload />Import file</Button><Button variant="ghost" size="small" disabled={validating} onClick={() => { setPlanText(JSON.stringify(examplePlan, null, 2)); setValidation(null); setPlanError(null) }}>Use example</Button></div></div><textarea id="plan-json" name="plan-json" autoComplete="off" value={planText} spellCheck={false} disabled={validating} onChange={event => { setPlanText(event.target.value); setValidation(null); setPlanError(null) }} aria-invalid={Boolean(planError)} aria-describedby={`plan-help${planError ? " plan-error" : ""}`} /><div className="plan-editor-footer"><span id="plan-help">Declarative commands and expected responses. No executable code.</span><Button variant="secondary" size="small" onClick={() => void validatePlan()} disabled={!connected || validating}>{validating ? <LoaderCircle className="spin" /> : <Check />}{validating ? 'Validating…' : 'Validate plan'}</Button></div></div><div className="plan-preview" aria-live="polite">{planError ? <div className="plan-invalid" role="alert"><AlertCircle /><h3>Plan needs attention</h3><p id="plan-error">{planError}</p></div> : validation ? <><div className="plan-validation-badge"><Check />Validated snapshot</div><h3>{validation.plan.name}</h3><p>{validation.test_count} tests · {validation.step_count} command steps</p><ol>{validation.plan.tests.map(test => <li key={test.id}><strong>{test.name}</strong><code>{test.id}</code><span>{test.steps.length} steps</span></li>)}</ol><div className="plan-fingerprint"><Fingerprint /><span>SHA-256<code title={validation.sha256}>{validation.sha256.slice(0, 16)}…</code></span></div></> : <><FileJson className="preview-symbol" /><h3>Preview before execution.</h3><p>Validate your JSON to inspect the saved test names, command count and definition fingerprint.</p><span className="preview-schema">Schema 1.0 · READ / WRITE / RESET / STATUS</span></>}</div></div>}
      <div className="campaign-submit"><p><Fingerprint />The validated plan and conditions are saved with every campaign.</p><Button type="submit" disabled={!connected || busy || working || validating || (custom && !validation)}>{working ? <LoaderCircle className="spin" /> : <Play />}{working ? 'Starting campaign…' : busy ? 'Worker busy' : 'Start campaign'}</Button></div>
    </form>
    <CampaignComparisonView campaigns={campaigns} catalog={catalog} connected={connected} loading={loading} onOpenRun={onOpenRun} />
    <div className="saved-campaigns-heading"><div><div className="eyebrow neutral">SAVED EXECUTIONS</div><h2>Resume with the evidence intact.</h2></div><Button variant="ghost" size="small" onClick={() => void reload()} disabled={loading || !connected}><RefreshCw className={loading ? 'spin' : ''} />Refresh campaigns</Button></div>
    {(loading && campaigns.length === 0) || inspecting ? <p className="loading-line" role="status"><LoaderCircle className="spin" />Loading saved campaign evidence…</p> : null}<div className="sr-only" role="status">{selected ? `${stateText(selected)}. ${selected.completed} of ${selected.total} campaign cases complete.` : ""}</div>{campaigns.length === 0 ? loading ? null : <div className="campaign-empty"><Layers3 /><h3>No campaigns recorded yet.</h3><p>Start a batch above. For example, 2 device behaviors with 2 seeds create 4 cases. Each case keeps its run evidence, including interrupted attempts.</p></div> : <div className="campaign-workspace"><aside className="campaign-list" aria-label="Saved campaigns">{campaigns.map(item => <button type="button" key={item.id} onClick={() => void inspect(item.id)} className={selected?.id === item.id ? 'selected' : ''} aria-pressed={selected?.id === item.id}><strong>{item.name}</strong><span>{timestamp(item.created_at)}</span><div><span>{stateText(item)}</span><span>{item.completed} / {item.total}</span></div></button>)}</aside>{selected && <section className="campaign-detail" aria-labelledby="campaign-result-heading"><div className="campaign-detail-heading"><div><span className={`campaign-state campaign-state-${selected.state}`}>{stateText(selected)}</span><h2 id="campaign-result-heading">{selected.name}</h2><p>{selected.plan_snapshot?.plan?.name ?? 'Standard catalog'} · {selected.completed} of {selected.total} cases complete</p></div><div className="campaign-controls">{['queued', 'running'].includes(selected.state) && <Button variant="secondary" size="small" onClick={() => void control('pause')} disabled={working || !connected || inspecting}><Pause />Pause after current run</Button>}{['paused', 'interrupted', 'error'].includes(selected.state) && <Button variant="secondary" size="small" onClick={() => void control('resume')} disabled={working || busy || !connected || inspecting}><Play />Resume remaining runs</Button>}<Button variant="ghost" size="small" asChild><a href={`/api/campaigns/${encodeURIComponent(selected.id)}/export`} download><ArrowDownToLine />Export campaign</a></Button></div></div><div className="campaign-progress" role="progressbar" aria-label="Completed campaign cases" aria-valuenow={selected.completed} aria-valuemin={0} aria-valuemax={selected.total}><div style={{ width: `${selected.total ? selected.completed / selected.total * 100 : 0}%` }} /></div>{selected.state === 'pausing' && <p className="campaign-state-note" role="status">The current run finishes first. Remaining cases will stay pending until you explicitly resume.</p>}{selected.error && <div className="inline-warning"><AlertCircle /><span>{selected.error}</span></div>}<p className="campaign-attempt-help">Attempt 1 is a case's first run. A retry creates Attempt 2 and keeps the earlier evidence.</p><div className="campaign-cases">{selected.cases.map((item, index) => <div className="campaign-case" key={item.id}><span className="campaign-case-number">{String(index + 1).padStart(2, '0')}</span><div><strong>{catalog?.scenarios.find(scenario => scenario.id === item.scenario)?.label ?? item.scenario}</strong><span>Seed {item.seed} · {item.state}</span><div className="attempt-links">{item.attempts.map((id, attempt) => <button type="button" key={id} onClick={() => onOpenRun(id)}>Attempt {attempt + 1}<ChevronRight /></button>)}</div></div><Button variant="ghost" size="small" disabled={!item.run_id} onClick={() => item.run_id && onOpenRun(item.run_id)}>Inspect<ArrowRight /></Button></div>)}</div><p className="campaign-record-note">A completed case means execution finished; its device assertions may still fail. Resuming preserves old attempts and runs remaining work with the saved plan.</p><details className="campaign-snapshot"><summary><Fingerprint />Inspect saved definition</summary><p>Engine fingerprint <code>{selected.engine_fingerprint}</code></p>{selected.plan_snapshot && <><p>Plan fingerprint <code>{selected.plan_snapshot.sha256}</code></p><pre>{JSON.stringify(selected.plan_snapshot.plan, null, 2)}</pre></>}</details></section>}</div>}
  </section>
}
