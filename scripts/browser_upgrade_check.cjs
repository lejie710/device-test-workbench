// Verify real plans, campaigns and evidence comparison in an isolated local runtime.
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const root = path.resolve(__dirname, '..');
const { chromium } = require(path.join(root, 'frontend/node_modules/playwright'));
const local = path.join(root, '.local');
const runtime = path.join(local, `browser-upgrade-runtime-${Date.now()}`);
const shots = path.join(root, 'docs/screenshots');
const results = path.join(root, 'docs/results');
for (const directory of [runtime, shots, results]) fs.mkdirSync(directory, { recursive: true });
const python = process.env.DTL_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function availablePort() {
  const probe = net.createServer();
  await new Promise((resolve, reject) => { probe.once('error', reject); probe.listen(0, '127.0.0.1', resolve); });
  const port = probe.address().port;
  await new Promise(resolve => probe.close(resolve));
  return port;
}

(async () => {
  const port = await availablePort();
  const base = `http://127.0.0.1:${port}`;
  const logPath = path.join(local, 'browser-upgrade-server.log');
  const log = fs.openSync(logPath, 'w');
  let server, browser;
  const errors = [], checks = [];
  async function stopServer() {
    if (server && server.exitCode === null && server.signalCode === null) {
      const exited = new Promise(resolve => server.once('exit', resolve));
      server.kill();
      await Promise.race([exited, sleep(5000)]);
    }
  }
  async function waitForCampaign(page, id) {
    for (let attempt = 0; attempt < 150; attempt++) {
      const response = await page.request.get(`${base}/api/campaigns/${id}`);
      assert.equal(response.status(), 200);
      const campaign = await response.json();
      if (!['queued', 'running', 'pausing'].includes(campaign.state)) return campaign;
      await sleep(100);
    }
    throw new Error('Campaign did not reach an inactive state');
  }
  try {
    server = spawn(python, ['-m', 'uvicorn', 'devicetestlab.app:app', '--host', '127.0.0.1', '--port', String(port)], {
      cwd: root, windowsHide: true, stdio: ['ignore', log, log],
      env: { ...process.env, DTL_RUNTIME_DIR: runtime, OMP_NUM_THREADS: '1', OPENBLAS_NUM_THREADS: '1', MKL_NUM_THREADS: '1' },
    });
    server.on('error', error => errors.push(error.message));
    let ready = false;
    for (let attempt = 0; attempt < 100; attempt++) {
      try { const response = await fetch(`${base}/api/health`); if (response.ok) { ready = true; break; } } catch {}
      if (server.exitCode !== null) throw new Error(fs.readFileSync(logPath, 'utf8'));
      await sleep(150);
    }
    assert.equal(ready, true, 'The local server should become healthy');
    browser = await chromium.launch({ channel: process.env.DTL_BROWSER_CHANNEL || 'chrome', headless: true, chromiumSandbox: true });
    const context = await browser.newContext({ viewport: { width: 1360, height: 1000 }, colorScheme: 'light', acceptDownloads: true });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${base}/#campaigns`);
    await page.getByRole('heading', { name: 'No campaigns recorded yet.' }).waitFor();
    await page.getByRole('button', { name: 'Custom JSON plan', exact: true }).click();
    await page.locator('#plan-json').fill('{"schema_version":"1.0","name":"Invalid","tests":[]}');
    await page.getByRole('button', { name: 'Validate plan', exact: true }).click();
    await page.getByRole('heading', { name: 'Plan needs attention', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Start campaign', exact: true }).isDisabled(), true);
    checks.push('Invalid declarative plan displays server validation errors and cannot execute');

    await page.getByRole('button', { name: 'Use example', exact: true }).click();
    const originalPlan = JSON.parse(await page.locator('#plan-json').inputValue());
    const importPath = path.join(local, 'browser-upgrade-import.json');
    fs.writeFileSync(importPath, JSON.stringify({ ...originalPlan, name: 'Imported integrity check' }, null, 2));
    await page.getByLabel('Import JSON plan file', { exact: true }).setInputFiles(importPath);
    await page.getByRole('button', { name: 'Validate plan', exact: true }).click();
    await page.getByText('Validated snapshot', { exact: true }).waitFor();
    assert.equal(JSON.parse(await page.locator('#plan-json').inputValue()).name, 'Imported integrity check');
    checks.push('JSON file import and validation produce an inspectable named snapshot');

    const createResponse = page.waitForResponse(response => response.url().endsWith('/api/campaigns') && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Start campaign', exact: true }).click();
    const initial = await (await createResponse).json();
    const completed = await waitForCampaign(page, initial.id);
    assert.equal(completed.state, 'completed'); assert.equal(completed.completed, 4); assert.equal(completed.cases.length, 4);
    assert.equal(completed.plan_snapshot.plan.name, 'Imported integrity check');
    assert.equal(completed.cases.every(item => item.attempts.length === 1), true);
    await page.locator('.campaign-detail').getByText('Complete', { exact: true }).waitFor();
    checks.push('Custom campaign executes all four scenario-and-seed cases and saves every attempt');
    const cleanCase = completed.cases.find(item => item.scenario === 'healthy' && item.seed === 3);
    const faultCase = completed.cases.find(item => item.scenario === 'ignored_write' && item.seed === 3);
    const clean = await (await page.request.get(`${base}/api/runs/${cleanCase.run_id}`)).json();
    const faulty = await (await page.request.get(`${base}/api/runs/${faultCase.run_id}`)).json();
    assert.equal(clean.passed, 1); assert.equal(faulty.failed, 1); assert.equal(faulty.errors, 0);
    assert.equal(faulty.suite_kind, 'custom'); assert.match(faulty.model_status, /not compatible/i);
    const campaignDownload = page.waitForEvent('download');
    await page.getByRole('link', { name: 'Export campaign', exact: true }).click();
    const downloaded = await campaignDownload;
    const downloadPath = path.join(local, 'checked-campaign-export.json');
    await downloaded.saveAs(downloadPath);
    const exported = JSON.parse(fs.readFileSync(downloadPath, 'utf8'));
    assert.equal((exported.campaign ?? exported).id, completed.id);
    checks.push('Campaign export includes the selected immutable campaign record');
    await page.screenshot({ path: path.join(shots, 'campaigns-light.png'), fullPage: true });
    await page.locator('.campaign-detail').screenshot({ path: path.join(shots, 'campaign-detail.png') });
    await page.locator('.plan-workspace').screenshot({ path: path.join(shots, 'plan-editor.png') });

    await page.locator('.campaign-case').filter({ hasText: 'Ignored write' }).filter({ hasText: 'Seed 3' }).getByRole('button', { name: 'Inspect', exact: true }).click();
    await page.getByRole('heading', { name: 'Failure detected', exact: true }).waitFor();
    await page.getByRole('button', { name: 'Saved custom plan', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Saved custom plan', exact: true }).isDisabled(), true);
    assert.match(await page.locator('.evidence-panel').innerText(), /Store a byte and clear it on reset/);
    checks.push('A campaign attempt opens the real trace, with catalog replay disabled for custom plans');

    await page.getByRole('button', { name: 'Run history', exact: true }).click();
    await page.locator('.evidence-comparison > summary').click();
    await page.locator('#compare-left').selectOption(clean.id);
    await page.locator('#compare-right').selectOption(faulty.id);
    await page.getByRole('button', { name: 'Compare evidence', exact: true }).click();
    await page.getByText('Conditions differ: interpret with care', { exact: true }).waitFor();
    const comparison = await (await page.request.get(`${base}/api/compare?left=${clean.id}&right=${faulty.id}`)).json();
    assert.equal(comparison.comparable, false); assert.equal(comparison.summary.changed_verdicts, 1);
    assert.equal(comparison.tests.find(item => item.id === 'byte_roundtrip').right.actual.value, 0);
    assert.equal(comparison.timing.modeled_cost_ms.delta, 0);
    await page.locator('.paired-evidence').waitFor();
    await page.screenshot({ path: path.join(shots, 'comparison-light.png'), fullPage: true });
    await page.locator('.comparison-result').screenshot({ path: path.join(shots, 'comparison-detail.png') });
    checks.push('Comparison shows changed conditions, one changed verdict, decisive values and a zero modeled-work delta');

    await page.setViewportSize({ width: 375, height: 900 });
    await page.screenshot({ path: path.join(shots, 'comparison-mobile.png'), fullPage: true });
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) {
      console.log(JSON.stringify(await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth, elements: Array.from(document.querySelectorAll('body *')).filter(el => { const bounds = el.getBoundingClientRect(); return bounds.width && (bounds.right > innerWidth + 1 || el.scrollWidth > el.clientWidth + 1); }).map(el => ({ tag: el.tagName, cls: el.className, right: Math.round(el.getBoundingClientRect().right), scroll: el.scrollWidth, client: el.clientWidth, overflow: getComputedStyle(el).overflowX })) })), null, 2));
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    assert.deepEqual(await page.evaluate(() => Array.from(document.querySelectorAll('body *')).filter(el => { const b=el.getBoundingClientRect(); return b.width && b.right>innerWidth+1 && getComputedStyle(el).position !== 'fixed' && !el.closest('.history-table-wrap,.comparison-condition-table'); }).map(el => ({tag:el.tagName, cls:el.className, right:Math.round(el.getBoundingClientRect().right)}))), []);
    await page.getByRole('button', { name: 'Campaigns', exact: true }).click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    assert.deepEqual(await page.evaluate(() => Array.from(document.querySelectorAll('body *')).filter(el => { const b=el.getBoundingClientRect(); return b.width && b.right>innerWidth+1 && getComputedStyle(el).position !== 'fixed' && !el.closest('.history-table-wrap,.comparison-condition-table'); }).map(el => ({tag:el.tagName, cls:el.className, right:Math.round(el.getBoundingClientRect().right)}))), []);
    await page.screenshot({ path: path.join(shots, 'campaigns-mobile.png'), fullPage: true });
    await page.setViewportSize({ width: 320, height: 850 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    assert.deepEqual(await page.evaluate(() => Array.from(document.querySelectorAll('body *')).filter(el => { const b=el.getBoundingClientRect(); return b.width && b.right>innerWidth+1 && getComputedStyle(el).position !== 'fixed' && !el.closest('.history-table-wrap,.comparison-condition-table'); }).map(el => ({tag:el.tagName, cls:el.className, right:Math.round(el.getBoundingClientRect().right)}))), []);
    checks.push('Campaign and comparison layouts avoid page-level overflow at 375px; campaign also checks 320px');
    await page.setViewportSize({ width: 1360, height: 1000 });
    await page.getByRole('button', { name: 'Switch to dark appearance', exact: true }).click();
    await page.screenshot({ path: path.join(shots, 'campaigns-dark.png'), fullPage: true });

    await page.getByRole('button', { name: 'Switch to light appearance', exact: true }).click();
    await page.getByRole('button', { name: 'Standard catalog', exact: true }).click();
    await page.locator('#campaign-name').fill('Pause and resume check');
    await page.locator('#campaign-seeds').fill('3, 4, 5, 6, 7');
    for (const checkbox of await page.locator('.scenario-choices input').all()) await checkbox.check();
    const pauseCreateResponse = page.waitForResponse(response => response.url().endsWith('/api/campaigns') && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Start campaign', exact: true }).click();
    const toPause = await (await pauseCreateResponse).json();
    await page.getByRole('button', { name: 'Pause after current run', exact: true }).click();
    const paused = await waitForCampaign(page, toPause.id);
    assert.equal(paused.state, 'paused'); assert.ok(paused.completed < paused.total);
    const preservedAttempts = paused.cases.filter(item => item.state === 'completed').map(item => ({ id: item.id, attempts: item.attempts }));
    await page.getByRole('button', { name: 'Resume remaining runs', exact: true }).waitFor();
    await page.locator('.campaign-detail').screenshot({ path: path.join(shots, 'campaign-paused.png') });
    const resumeResponse = page.waitForResponse(response => response.url().endsWith(`/api/campaigns/${toPause.id}/resume`) && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Resume remaining runs', exact: true }).click();
    assert.ok((await resumeResponse).ok());
    const resumed = await waitForCampaign(page, toPause.id);
    assert.equal(resumed.state, 'completed'); assert.equal(resumed.completed, 25);
    for (const before of preservedAttempts) assert.deepEqual(resumed.cases.find(item => item.id === before.id).attempts, before.attempts);
    assert.equal(resumed.cases.every(item => item.attempts.length === 1), true);
    checks.push('Pause waits for the current run; explicit Resume completes the remaining 25-case sweep without repeating completed attempts');

    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(results, 'browser-upgrade-check.json'), JSON.stringify({
      checked_at: new Date().toISOString(), status: 'passed', checks, browser: await browser.version(),
      viewport: [1360, 1000], mobile_widths: [375, 320], context: 'Actual API and production frontend; temporary local runtime; isolated headless browser',
    }, null, 2));
    console.log(JSON.stringify({ status: 'passed', checks, screenshots: shots }, null, 2));
  } finally {
    if (browser) await browser.close();
    await stopServer();
    fs.closeSync(log);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
