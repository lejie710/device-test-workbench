// Exercise campaign comparison with executed simulator evidence in an isolated runtime.
// HTTP delays and an unavailable response test interface recovery, never device outcomes.
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const root = path.resolve(__dirname, '..');
const { chromium } = require(path.join(root, 'frontend/node_modules/playwright'));
const local = path.join(root, '.local');
const runtime = path.join(local, `browser-campaign-comparison-runtime-${Date.now()}`);
const shots = path.join(root, 'docs/screenshots');
const results = path.join(root, 'docs/results');
for (const directory of [runtime, shots, results]) fs.mkdirSync(directory, { recursive: true });
const python = process.env.DTL_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const plan = {
  schema_version: '1.0', name: 'Saved byte integrity check', tests: [{
    id: 'byte_roundtrip', name: 'Store a byte and read it back', steps: [
      { command: { op: 'WRITE', address: 3, value: 165 }, expected: { ok: true } },
      { command: { op: 'READ', address: 3 }, expected: { ok: true, value: 165 } },
    ],
  }],
};

async function availablePort() {
  const probe = net.createServer();
  await new Promise((resolve, reject) => { probe.once('error', reject); probe.listen(0, '127.0.0.1', resolve); });
  const port = probe.address().port;
  await new Promise(resolve => probe.close(resolve));
  return port;
}

(async () => {
  const port = await availablePort(), base = `http://127.0.0.1:${port}`;
  const logPath = path.join(local, 'browser-campaign-comparison-server.log');
  const log = fs.openSync(logPath, 'w');
  let server, browser;
  const errors = [], checks = [];
  async function stopServer() {
    if (server && server.exitCode === null && server.signalCode === null) {
      const exited = new Promise(resolve => server.once('exit', resolve));
      server.kill();
      await Promise.race([exited, sleep(5000)]);
      assert.ok(server.exitCode !== null || server.signalCode !== null, 'Owned API process stopped');
    }
  }
  async function startServer() {
    server = spawn(python, ['-m', 'uvicorn', 'devicetestlab.app:app', '--host', '127.0.0.1', '--port', String(port)], {
      cwd: root, windowsHide: true, stdio: ['ignore', log, log],
      env: { ...process.env, DTL_RUNTIME_DIR: runtime, OMP_NUM_THREADS: '1', OPENBLAS_NUM_THREADS: '1', MKL_NUM_THREADS: '1' },
    });
    server.on('error', error => errors.push(error.message));
    for (let attempt = 0; attempt < 100; attempt++) {
      try { if ((await fetch(`${base}/api/health`)).ok) return; } catch {}
      if (server.exitCode !== null) throw new Error(fs.readFileSync(logPath, 'utf8'));
      await sleep(150);
    }
    throw new Error('The isolated API did not become healthy');
  }
  async function get(relative) {
    const response = await fetch(`${base}/api${relative}`);
    assert.equal(response.status, 200, `GET ${relative}`);
    return response.json();
  }
  async function post(relative, body) {
    const response = await fetch(`${base}/api${relative}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
    const result = await response.json();
    assert.ok(response.ok, `${relative}: ${JSON.stringify(result)}`);
    return result;
  }
  async function waitForCampaign(id, predicate = item => !['queued', 'running', 'pausing'].includes(item.state)) {
    for (let attempt = 0; attempt < 500; attempt++) {
      const item = await get(`/campaigns/${id}`);
      if (predicate(item)) return item;
      await sleep(20);
    }
    throw new Error(`Campaign ${id} did not reach the required state`);
  }
  async function create(name, seeds = [3, 4], scenarios = ['healthy', 'ignored_write'], definition = plan) {
    const item = await post('/campaigns', { name, seeds, scenarios, plan: definition });
    const done = await waitForCampaign(item.id);
    assert.equal(done.state, 'completed');
    return done;
  }
  try {
    await startServer();
    browser = await chromium.launch({ channel: process.env.DTL_BROWSER_CHANNEL || 'chrome', headless: true, chromiumSandbox: true });
    const context = await browser.newContext({ viewport: { width: 1360, height: 1000 }, colorScheme: 'light', acceptDownloads: true });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${base}/#campaigns`);
    const panel = page.locator('.campaign-comparison');
    await panel.locator(':scope > summary').focus();
    await page.keyboard.press('Enter');
    assert.equal(await panel.getAttribute('open'), '');
    await panel.getByText(/Save at least two campaigns/).waitFor();
    assert.equal(await panel.getByRole('button', { name: 'Compare campaigns', exact: true }).isDisabled(), true);
    checks.push('Keyboard opens the comparison panel; empty history explains how to create a comparable pair');

    const reference = await create('Reference integrity sweep');
    await page.reload();
    await panel.locator(':scope > summary').click();
    await panel.getByText(/Save at least two campaigns/).waitFor();
    assert.equal(await panel.getByRole('button', { name: 'Compare campaigns', exact: true }).isDisabled(), true);
    const repeat = await create('Repeat integrity sweep');
    const membership = await create('Changed seed coverage', [3, 5]);
    const alteredPlan = structuredClone(plan);
    alteredPlan.name = 'Different expected response';
    alteredPlan.tests[0].steps[1].expected.value = 0;
    const incompatible = await create('Changed plan definition', [3, 4], ['healthy', 'ignored_write'], alteredPlan);
    await page.reload();
    await panel.locator(':scope > summary').click();
    await page.locator('#campaign-compare-left').selectOption(reference.id);
    await page.locator('#campaign-compare-right').selectOption(reference.id);
    await panel.getByText(/Choose two different campaigns/).waitFor();
    assert.equal(await panel.getByRole('button', { name: 'Compare campaigns', exact: true }).isDisabled(), true);
    const same = await get(`/campaign-compare?left=${reference.id}&right=${reference.id}`);
    assert.equal(same.summary.comparable_cases, 0);
    checks.push('One-campaign and same-campaign selections cannot imply independent evidence');

    async function compare(left, right) {
      await page.locator('#campaign-compare-left').selectOption(left);
      await page.locator('#campaign-compare-right').selectOption(right);
      const response = page.waitForResponse(item => item.url().includes('/api/campaign-compare?') && item.request().method() === 'GET');
      await panel.getByRole('button', { name: 'Compare campaigns', exact: true }).click();
      const report = await (await response).json();
      await panel.locator('.campaign-comparison-result').waitFor();
      return report;
    }
    const equal = await compare(reference.id, repeat.id);
    assert.equal(equal.summary.comparable_cases, 4);
    assert.equal(equal.summary.not_compared_cases, 0);
    assert.equal(equal.summary.changed_cases, 0);
    // The plan writes address 3. Seed 3 injects its ignored write there; seed 4 affects address 4.
    assert.equal(equal.summary.persistent_failures, 1);
    assert.equal(equal.summary.unchanged_passes, 3);
    assert.match(await panel.locator('.campaign-compare-summary').innerText(), /4\s*\/ 4/);
    await panel.getByText('Matching cases and complete evidence', { exact: true }).waitFor();
    await page.locator('#campaign-case-filter').selectOption('changed');
    await panel.getByText('No changed verdicts within the comparable case set.', { exact: true }).waitFor();
    await page.locator('#campaign-case-filter').selectOption('all');
    await panel.locator('.campaign-compare-cases > button').filter({ hasText: 'Ignored write' }).filter({ hasText: 'Seed 3' }).click();
    assert.equal(await panel.locator('.paired-evidence pre').nth(2).innerText(), '{\n  "ok": true,\n  "value": 0\n}');
    assert.match(await panel.locator('.campaign-compare-attempts').innerText(), /Recorded attempts: 1\. 0 earlier attempts retained/);
    checks.push('Complete matching pairs use API counts, preserve recurring failures and show actual decisive response evidence');

    const downloadEvent = page.waitForEvent('download');
    await panel.getByRole('button', { name: 'Export comparison JSON', exact: true }).click();
    const download = await downloadEvent, exportPath = path.join(local, 'checked-campaign-comparison.json');
    await download.saveAs(exportPath);
    const displayedExport = JSON.parse(fs.readFileSync(exportPath, 'utf8'));
    assert.deepEqual(displayedExport, equal);
    assert.deepEqual(displayedExport, await get(`/campaign-compare/export?left=${reference.id}&right=${repeat.id}`));
    checks.push('Downloaded JSON exactly equals the displayed snapshot and the API export for completed campaigns');

    const wrong = await compare(reference.id, incompatible.id);
    assert.equal(wrong.summary.comparable_cases, 0);
    assert.equal(wrong.summary.not_compared_cases, 4);
    assert.ok(wrong.cases.some(item => item.comparison.summary.changed_verdicts > 0));
    await panel.getByText('No cases qualify for failure-change totals', { exact: true }).waitFor();
    assert.equal(await panel.locator('.campaign-compare-deltas').count(), 0);
    assert.match(await panel.locator('.campaign-compare-summary').innerText(), /—/);
    await panel.locator('.condition-details').first().locator('summary').click();
    assert.ok(await panel.locator('.comparison-condition-table .condition-changed').count() > 0);
    await panel.screenshot({ path: path.join(shots, 'campaign-comparison-conditions.png') });
    checks.push('Changed definitions expose saved condition differences and exclude their changed verdicts from failure-change totals');

    let releaseDelayed, delayedStarted;
    const began = new Promise(resolve => { delayedStarted = resolve; });
    const gate = new Promise(resolve => { releaseDelayed = resolve; });
    const delayRoute = async route => { const response = await route.fetch(); delayedStarted(); await gate; await route.fulfill({ response }); };
    await page.route('**/api/campaign-compare?*', delayRoute);
    await page.locator('#campaign-compare-right').selectOption(repeat.id);
    await panel.getByRole('button', { name: 'Compare campaigns', exact: true }).click();
    await began;
    await panel.getByText('Reading saved cases and checking their conditions…', { exact: true }).waitFor();
    assert.equal(await page.locator('#campaign-compare-right').isEnabled(), true);
    await page.locator('#campaign-compare-right').selectOption(membership.id);
    releaseDelayed();
    await sleep(180);
    assert.equal(await panel.locator('.campaign-comparison-result').count(), 0, 'An older response must not replace a new selection');
    await page.unroute('**/api/campaign-compare?*', delayRoute);
    checks.push('Loading feedback remains interactive; changing a selector prevents a stale asynchronous response from appearing');

    await compare(reference.id, repeat.id);
    const narrowerList = async route => {
      const response = await route.fetch(), data = await response.json();
      await route.fulfill({ response, json: { campaigns: data.campaigns.filter(item => item.id !== reference.id) } });
    };
    await page.route('**/api/campaigns', narrowerList);
    await page.getByRole('button', { name: 'Refresh campaigns', exact: true }).click();
    await page.waitForFunction(id => document.getElementById('campaign-compare-left').value !== id, reference.id);
    assert.equal(await panel.locator('.campaign-comparison-result').count(), 0, 'Automatic selection replacement must clear the older report');
    await page.unroute('**/api/campaigns', narrowerList);
    await page.getByRole('button', { name: 'Refresh campaigns', exact: true }).click();
    await page.locator(`#campaign-compare-left option[value="${reference.id}"]`).waitFor({ state: 'attached' });
    await page.locator('#campaign-compare-left').selectOption(reference.id);
    checks.push('A refreshed saved-campaign list that drops a selection clears its report before choosing a replacement');

    const errorRoute = route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Comparison service temporarily unavailable.' }) });
    await page.route('**/api/campaign-compare?*', errorRoute);
    await panel.getByRole('button', { name: 'Compare campaigns', exact: true }).click();
    await panel.getByRole('alert').filter({ hasText: 'Comparison service temporarily unavailable.' }).waitFor();
    await page.unroute('**/api/campaign-compare?*', errorRoute);
    const mixed = await compare(reference.id, membership.id);
    assert.equal(mixed.summary.comparable_cases, 2);
    assert.equal(mixed.summary.total_cases, 6);
    assert.equal(mixed.summary.not_compared_cases, 4);
    assert.equal(mixed.summary.added_cases, 2);
    assert.equal(mixed.summary.removed_cases, 2);
    await page.locator('#campaign-case-filter').selectOption('not_compared');
    assert.equal(await panel.locator('.campaign-compare-cases > button').count(), 4);
    assert.equal(await panel.locator('.paired-evidence').count(), 0);
    await panel.getByText('No case with this scenario and seed.', { exact: true }).waitFor();
    await page.locator('#campaign-case-filter').selectOption('all');
    await panel.locator('.campaign-compare-cases > button').filter({ hasText: 'Ignored write' }).filter({ hasText: 'Seed 3' }).click();
    checks.push('An unavailable response has actionable feedback and retry works; added and removed cases stay outside the paired denominator');

    await panel.screenshot({ path: path.join(shots, 'campaign-comparison-light.png') });
    await panel.locator('.campaign-comparison-result').screenshot({ path: path.join(shots, 'campaign-comparison-detail.png') });
    await panel.locator('.campaign-case-evidence').screenshot({ path: path.join(shots, 'campaign-comparison-evidence.png') });
    await page.setViewportSize({ width: 780, height: 1000 });
    await panel.locator('.campaign-compare-overview').screenshot({ path: path.join(shots, 'campaign-comparison-summary.png') });
    await page.setViewportSize({ width: 1360, height: 1000 });
    await page.getByRole('button', { name: 'Switch to dark appearance', exact: true }).click();
    await panel.screenshot({ path: path.join(shots, 'campaign-comparison-dark.png') });
    for (const width of [375, 320]) {
      await page.setViewportSize({ width, height: 900 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, `Page overflow at ${width}px`);
      await panel.screenshot({ path: path.join(shots, `campaign-comparison-dark-${width}.png`) });
    }
    await page.getByRole('button', { name: 'Switch to light appearance', exact: true }).click();
    for (const width of [375, 320]) {
      await page.setViewportSize({ width, height: 900 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, `Light page overflow at ${width}px`);
      await panel.screenshot({ path: path.join(shots, `campaign-comparison-mobile-${width}.png`) });
    }
    await page.setViewportSize({ width: 1360, height: 1000 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await panel.getByRole('button', { name: 'Export comparison JSON', exact: true }).focus();
    await page.keyboard.press('Tab');
    await page.keyboard.press('Shift+Tab');
    assert.notEqual(await panel.getByRole('button', { name: 'Export comparison JSON', exact: true }).evaluate(element => getComputedStyle(element).outlineStyle), 'none');
    await panel.locator('.paired-evidence').getByRole('button', { name: 'Full trace', exact: true }).last().click();
    await page.getByRole('heading', { name: 'Failure detected', exact: true }).waitFor();
    assert.match(await page.locator('.evidence-panel').innerText(), /Store a byte and read it back/);
    checks.push('Full trace opens the selected real attempt; light/dark layouts fit 375px and 320px, with keyboard focus and reduced motion');

    const allScenarios = ['healthy', 'corrupt_read', 'ignored_write', 'stuck_state', 'timeout'];
    const allSeeds = [3, 4, 5, 6, 7];
    const full = await create('Complete catalog reference', allSeeds, allScenarios, null);
    const active = await post('/campaigns', { name: 'Paused catalog comparison', seeds: allSeeds, scenarios: allScenarios, plan: null });
    await waitForCampaign(active.id, item => item.completed >= 1);
    await post(`/campaigns/${active.id}/pause`);
    const paused = await waitForCampaign(active.id);
    assert.equal(paused.state, 'paused');
    assert.ok(paused.completed > 0 && paused.completed < paused.total);
    await page.goto(`${base}/#campaigns`);
    await page.reload();
    await panel.locator(':scope > summary').click();
    const partial = await compare(full.id, paused.id);
    assert.equal(partial.summary.comparable_cases, paused.completed);
    assert.equal(partial.summary.not_compared_cases, 25 - paused.completed);
    await page.locator('#campaign-case-filter').selectOption('not_compared');
    await panel.getByText('No attempt recorded. This case has not produced evidence.', { exact: true }).waitFor();
    await panel.screenshot({ path: path.join(shots, 'campaign-comparison-incomplete.png') });
    checks.push('A real paused campaign counts completed comparable cases while pending cases remain explicitly not compared');

    // Crash only the owned isolated API while a saved attempt is running, then use actual recovery.
    const interrupted = await post('/campaigns', { name: 'Recovered catalog comparison', seeds: allSeeds, scenarios: allScenarios, plan: null });
    const running = await waitForCampaign(interrupted.id, item => item.cases.some(row => row.state === 'running'));
    assert.ok(running.cases.some(item => item.attempts.length));
    await stopServer();
    await startServer();
    const recovered = await get(`/campaigns/${interrupted.id}`);
    assert.equal(recovered.state, 'interrupted');
    const retryCase = recovered.cases.find(item => item.state === 'interrupted');
    assert.ok(retryCase, 'The interrupted attempt must remain attached to its case');
    const oldRun = await get(`/runs/${retryCase.run_id}`);
    assert.equal(oldRun.state, 'interrupted');
    await post(`/campaigns/${recovered.id}/resume`);
    const resumed = await waitForCampaign(recovered.id);
    assert.equal(resumed.state, 'completed');
    assert.deepEqual(await get(`/runs/${oldRun.id}`), oldRun, 'Resumption preserves the interrupted evidence exactly');
    await page.reload();
    await panel.locator(':scope > summary').click();
    const retryReport = await compare(full.id, resumed.id);
    const retryRow = retryReport.cases.find(item => item.scenario === retryCase.scenario && item.seed === retryCase.seed);
    assert.equal(retryRow.right.attempt_count, 2);
    assert.notEqual(retryRow.right.run_id, oldRun.id);
    await panel.locator('.campaign-compare-cases > button').filter({ hasText: retryCase.scenario === 'healthy' ? 'Healthy device' : (await get('/catalog')).scenarios.find(item => item.id === retryCase.scenario).label }).filter({ hasText: `Seed ${retryCase.seed}` }).click();
    assert.match(await panel.locator('.campaign-compare-attempts').innerText(), /Recorded attempts: 2\. 1 earlier attempt retained/);
    await panel.locator('.campaign-case-evidence').screenshot({ path: path.join(shots, 'campaign-comparison-latest-attempt.png') });
    checks.push('Actual interruption and explicit resumption preserve the old run; comparison uses attempt 2 and displays the retained earlier attempt');

    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(results, 'browser-campaign-comparison-check.json'), JSON.stringify({
      checked_at: new Date().toISOString(), status: 'passed', checks, browser: await browser.version(),
      viewport: [1360, 1000], mobile_widths: [375, 320], context: 'Actual API and production frontend; executed simulator outcomes; temporary runtime; isolated headless browser',
      controlled_transport_cases: ['Delayed real response for stale-selection check', 'Narrowed real campaign list to simulate a selection leaving the saved-history window', 'HTTP 503 response for recovery feedback'],
      matched_snapshot: mixed.summary,
    }, null, 2));
    console.log(JSON.stringify({ status: 'passed', checks, screenshots: shots }, null, 2));
  } finally {
    if (browser) await browser.close();
    await stopServer();
    fs.closeSync(log);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
