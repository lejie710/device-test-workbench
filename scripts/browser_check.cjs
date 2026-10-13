// Exercise the real local application in an isolated runtime and browser profile.
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const root = path.resolve(__dirname, '..');
const { chromium } = require(path.join(root, 'frontend/node_modules/playwright'));
const local = path.join(root, '.local');
const runtime = path.join(local, `browser-runtime-${Date.now()}`);
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
  const log = fs.openSync(path.join(local, 'browser-server.log'), 'w');
  let server, browser;
  const errors = [];
  const checks = [];
  function startServer() {
    server = spawn(python, ['-m', 'uvicorn', 'devicetestlab.app:app', '--host', '127.0.0.1', '--port', String(port)], {
      cwd: root, windowsHide: true, stdio: ['ignore', log, log],
      env: { ...process.env, DTL_RUNTIME_DIR: runtime, OMP_NUM_THREADS: '1', OPENBLAS_NUM_THREADS: '1', MKL_NUM_THREADS: '1' }
    });
    server.on('error', error => errors.push(error.message));
  }
  async function stopServer() {
    if (server && server.exitCode === null && server.signalCode === null) {
      const exited = new Promise(resolve => server.once('exit', resolve));
      server.kill();
      await Promise.race([exited, sleep(5000)]);
    }
  }
  async function healthy() {
    for (let attempt = 0; attempt < 100; attempt++) {
      try { const response = await fetch(`${base}/api/health`); if (response.ok && (await response.json()).status === 'ok') return; } catch {}
      if (server.exitCode !== null) throw new Error(`Server exited: ${fs.readFileSync(path.join(local, 'browser-server.log'), 'utf8')}`);
      await sleep(150);
    }
    throw new Error('Server did not become healthy');
  }
  try {
    startServer();
    await healthy();
    browser = await chromium.launch({ channel: process.env.DTL_BROWSER_CHANNEL || 'chrome', headless: true, chromiumSandbox: true });
    const context = await browser.newContext({ viewport: { width: 1360, height: 1000 }, colorScheme: 'light', acceptDownloads: true });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base);
    await page.getByRole('button', { name: 'Run tests', exact: true }).waitFor();
    await page.getByRole('heading', { name: 'Run a healthy device first.' }).waitFor();
    assert.equal(await page.locator('#scenario').inputValue(), 'healthy');
    assert.equal(await page.locator('#strategy').inputValue(), 'default');
    assert.equal(await page.locator('#seed').inputValue(), '3');
    assert.match(await page.locator('.bench-intro').innerText(), /software-simulated memory device[\s\S]*does not test your laptop's RAM/);
    assert.equal(await page.locator('#seed').getAttribute('aria-describedby'), 'seed-help');
    assert.match(await page.locator('#seed-help').innerText(), /Corrupted read[\s\S]*Ignored write[\s\S]*Healthy device ignores seed/);
    await page.screenshot({ path: path.join(shots, 'empty-light.png'), fullPage: true });
    checks.push('Fresh runtime explains the simulated device and seed, with Healthy / Catalog / 3 defaults');

    async function run(scenario, strategy) {
      await page.locator('#scenario').selectOption(scenario);
      await page.locator('#strategy').selectOption(strategy);
      await page.locator('#seed').fill('3');
      const responsePromise = page.waitForResponse(response => response.url().endsWith('/api/runs') && response.request().method() === 'POST');
      await page.getByRole('button', { name: 'Run tests', exact: true }).click();
      const initial = await (await responsePromise).json();
      for (let attempt = 0; attempt < 100; attempt++) {
        const current = await (await page.request.get(`${base}/api/runs/${initial.id}`)).json();
        if (!['running', 'queued'].includes(current.state)) {
          await page.getByRole('heading', { name: current.failed ? 'Failure detected' : 'All checks passed', exact: true }).waitFor();
          return current;
        }
        await sleep(100);
      }
      throw new Error('Test execution did not finish');
    }

    const clean = await run('healthy', 'default');
    assert.equal(clean.passed, 14); assert.equal(clean.failed, 0); assert.equal(clean.state, 'completed');
    checks.push('Healthy device completes all 14 checks without failures');
    const failed = await run('corrupt_read', 'ml');
    assert.equal(failed.failed, 7); assert.equal(failed.errors, 0); assert.equal(failed.tests.length, 14);
    assert.match(failed.model_status, /fallback/i);
    checks.push('Faulty device produces seven observed failures and an explicit untrained-ML fallback');
    await page.screenshot({ path: path.join(shots, 'bench-light.png'), fullPage: true });
    await page.locator('.evidence-panel').screenshot({ path: path.join(shots, 'evidence-detail.png') });
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('link', { name: 'Export JSON' }).click();
    const download = await downloadPromise;
    const exportPath = path.join(local, 'checked-run-export.json');
    await download.saveAs(exportPath);
    assert.deepEqual(JSON.parse(fs.readFileSync(exportPath, 'utf8')), failed);
    checks.push('Downloaded JSON exactly preserves the recorded evidence');

    await page.getByRole('button', { name: 'Switch to dark appearance' }).click();
    await page.screenshot({ path: path.join(shots, 'bench-dark.png'), fullPage: true });
    await page.getByRole('button', { name: 'Switch to light appearance' }).click();
    await page.getByRole('button', { name: 'Run history', exact: true }).click();
    await page.locator('.history-table tbody tr').first().waitFor();
    assert.equal(await page.locator('.history-table tbody tr').count(), 2);
    await page.getByRole('button', { name: 'Clean runs', exact: true }).click();
    assert.equal(await page.locator('.history-table tbody tr').count(), 1);
    await page.getByRole('button', { name: 'All runs', exact: true }).click();
    await page.screenshot({ path: path.join(shots, 'history-light.png'), fullPage: true });
    checks.push('History persists both runs and filters clean results');

    await page.getByRole('button', { name: 'Ordering lab', exact: true }).click();
    await page.getByRole('button', { name: 'Train and evaluate', exact: true }).click();
    await page.locator('.comparison-card').waitFor({ timeout: 90000 });
    const report = await (await page.request.get(`${base}/api/experiment`)).json();
    assert.equal(report.state, 'completed'); assert.equal(report.dataset.rows, 2940);
    assert.equal(report.methods.length, 3);
    for (const method of report.methods) { assert.equal(method.total_tests, 840); assert.equal(method.failures_found, 210); }
    await page.screenshot({ path: path.join(shots, 'ordering-light.png'), fullPage: true });
    await page.locator('.comparison-card').screenshot({ path: path.join(shots, 'ordering-comparison.png') });
    const publishedReport = JSON.parse(fs.readFileSync(path.join(results, 'experiment.json'), 'utf8'));
    assert.deepEqual(report.methods, publishedReport.methods, 'The fixed benchmark should reproduce the published method metrics');
    fs.writeFileSync(path.join(local, 'checked-experiment.json'), JSON.stringify(report, null, 2));
    checks.push('UI reproduces the published 2,940-row benchmark metrics without replacing its dated report');

    await page.getByRole('button', { name: 'Test bench', exact: true }).click();
    const trained = await run('timeout', 'ml');
    assert.equal(trained.failed, 8); assert.equal(trained.errors, 0);
    assert.doesNotMatch(trained.model_status, /fallback/i);
    const preserved = await (await page.request.get(`${base}/api/runs/${clean.id}`)).json();
    assert.deepEqual(preserved, clean);
    checks.push('Trained model runs the full timeout suite and earlier evidence remains unchanged');

    await page.setViewportSize({ width: 375, height: 900 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    await page.screenshot({ path: path.join(shots, 'bench-mobile.png'), fullPage: true });
    checks.push('Phone-width test bench has no page-level horizontal overflow');
    await page.setViewportSize({ width: 1360, height: 1000 });

    await page.route('**/api/**', route => route.abort());
    await page.reload();
    await page.getByText('Connection needs attention', { exact: true }).waitFor();
    await page.screenshot({ path: path.join(shots, 'offline-light.png'), fullPage: true });
    await page.unroute('**/api/**');
    await page.getByRole('button', { name: 'Reconnect', exact: true }).click();
    await page.getByRole('button', { name: 'Run tests', exact: true }).waitFor();
    checks.push('Unavailable API produces a clear error and reconnect recovers');

    await stopServer(); startServer(); await healthy();
    const restored = await (await fetch(`${base}/api/runs/${clean.id}`)).json();
    assert.deepEqual(restored, clean);
    checks.push('Server restart preserves completed run evidence');
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(results, 'browser-check.json'), JSON.stringify({
      checked_at: new Date().toISOString(), status: 'passed', checks, browser: await browser.version(),
      viewport: [1360, 1000], mobile_width: 375, context: 'Local simulator; temporary runtime and isolated headless Chrome profile',
    }, null, 2));
    console.log(JSON.stringify({ status: 'passed', checks, screenshots: shots, browser: await browser.version() }, null, 2));
  } finally {
    if (browser) await browser.close();
    await stopServer();
    fs.closeSync(log);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
