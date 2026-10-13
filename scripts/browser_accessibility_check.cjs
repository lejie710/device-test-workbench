// Exercise keyboard, validation, narrow layouts and delayed UI responses against the real app.
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const root = path.resolve(__dirname, '..');
const { chromium } = require(path.join(root, 'frontend/node_modules/playwright'));
const local = path.join(root, '.local');
const runtime = path.join(local, `browser-accessibility-runtime-${Date.now()}`);
const shots = path.join(local, 'browser-accessibility-shots');
for (const directory of [runtime, shots]) fs.mkdirSync(directory, { recursive: true });
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
  const logPath = path.join(local, 'browser-accessibility-server.log');
  const log = fs.openSync(logPath, 'w');
  let server, browser;
  const errors = [], checks = [];
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
  async function noPageOverflow(page, context) {
    const overflow = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth,
      outside: Array.from(document.querySelectorAll('body *')).filter(el => {
        const bounds = el.getBoundingClientRect();
        return bounds.width && bounds.right > innerWidth + 1 && getComputedStyle(el).position !== 'fixed' && !el.closest('.history-table-wrap,.comparison-condition-table');
      }).map(el => ({ tag: el.tagName, className: el.className, right: Math.round(el.getBoundingClientRect().right) })),
    }));
    assert.ok(overflow.scroll <= overflow.width + 1, `${context}: ${JSON.stringify(overflow)}`);
    assert.deepEqual(overflow.outside, [], context);
  }
  async function readableText(page, selectors) {
    const measurements = await page.evaluate(selectors => {
      const rgba = color => color.match(/[\d.]+/g).map(Number);
      const over = (top, bottom) => top.slice(0, 3).map((value, index) => value * (top[3] ?? 1) + bottom[index] * (1 - (top[3] ?? 1)));
      const luminance = rgb => rgb.map(value => { value /= 255; return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4; }).reduce((total, value, index) => total + value * [.2126, .7152, .0722][index], 0);
      return selectors.flatMap(selector => {
        const elements = Array.from(document.querySelectorAll(selector)).filter(element => element.checkVisibility());
        if (!elements.length) return [{ selector, error: 'Expected visible text was not found' }];
        return elements.map(element => {
          if (!element.checkVisibility() || element.closest(':disabled,[aria-hidden="true"],.sr-only')) return { selector, error: 'Expected informative, enabled text' };
          const ancestors = [];
          for (let current = element; current; current = current.parentElement) ancestors.unshift(current);
          let background = [255, 255, 255];
          for (const ancestor of ancestors) background = over(rgba(getComputedStyle(ancestor).backgroundColor), background);
          const foreground = over(rgba(getComputedStyle(element).color), background);
          const first = luminance(foreground), second = luminance(background);
          return { selector, theme: document.documentElement.dataset.theme, foreground, background, ratio: (Math.max(first, second) + .05) / (Math.min(first, second) + .05) };
        });
      });
    }, selectors);
    for (const measurement of measurements) {
      assert.equal(measurement.error, undefined, JSON.stringify(measurement));
      assert.ok(measurement.ratio >= 4.5, `Normal text contrast must reach 4.5:1 without rounding: ${JSON.stringify(measurement)}`);
    }
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
    assert.equal(ready, true, 'The isolated local server should become healthy');
    browser = await chromium.launch({ channel: process.env.DTL_BROWSER_CHANNEL || 'chrome', headless: true, chromiumSandbox: true });
    const context = await browser.newContext({ viewport: { width: 1360, height: 1000 }, colorScheme: 'light' });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${base}/#campaigns`);
    await page.getByRole('heading', { name: 'No campaigns recorded yet.' }).waitFor();
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.activeElement?.textContent), 'Skip to main content');
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => document.activeElement?.id), 'main-content');
    checks.push('Keyboard skip link moves focus into the main workspace');

    const seeds = page.locator('#campaign-seeds');
    for (const [input, message] of [['3, 3', 'Each seed must be unique.'], ['', 'Enter comma-separated whole-number seeds'], ['-1', 'Enter comma-separated whole-number seeds']]) {
      await seeds.fill(input);
      await page.locator('#seeds-error').waitFor();
      assert.match(await page.locator('#seeds-error').innerText(), new RegExp(message.replace('.', '\\.')));
      assert.equal(await seeds.getAttribute('aria-invalid'), 'true');
      await page.getByRole('button', { name: 'Start campaign', exact: true }).click();
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'campaign-seeds');
    }
    await seeds.fill('3');
    assert.equal(await page.locator('#seeds-error').count(), 0);
    for (const checkbox of await page.locator('.scenario-choices input').all()) await checkbox.uncheck();
    await page.getByRole('button', { name: 'Start campaign', exact: true }).click();
    assert.equal(await page.evaluate(() => document.activeElement?.matches('.scenario-choices input')), true);
    assert.match(await page.locator('#scenarios-error').innerText(), /Choose at least one scenario/);
    await page.getByLabel('Healthy device', { exact: true }).check();
    await seeds.fill(Array.from({ length: 26 }, (_, index) => index).join(', '));
    await page.getByRole('button', { name: 'Start campaign', exact: true }).click();
    assert.match(await page.locator('#cases-error').innerText(), /at most 25/);
    assert.equal(await page.evaluate(() => document.activeElement?.id), 'campaign-seeds');
    await page.screenshot({ path: path.join(shots, 'campaign-validation.png'), fullPage: true });
    await seeds.fill('3');
    checks.push('Invalid, duplicate and missing campaign inputs show associated explanations and focus the field on submit');

    await page.getByRole('button', { name: 'Custom JSON plan', exact: true }).click();
    const longName = 'LongUnbrokenTestName'.repeat(5).slice(0, 80);
    const plan = { schema_version: '1.0', name: longName, tests: [{ id: 'long_identifier_'.repeat(4).slice(0, 48), name: longName, steps: [
      { command: { op: 'WRITE', address: 3, value: 165 }, expected: { ok: true } },
      { command: { op: 'READ', address: 3 }, expected: { ok: true, value: 165 } },
    ] }] };
    await page.locator('#plan-json').fill(JSON.stringify(plan, null, 2));
    await page.getByRole('button', { name: 'Validate plan', exact: true }).click();
    await page.getByText('Validated snapshot', { exact: true }).waitFor();
    for (const width of [375, 320]) {
      await page.setViewportSize({ width, height: 900 });
      await noPageOverflow(page, `Custom plan preview at ${width}px`);
    }
    await page.screenshot({ path: path.join(shots, 'long-plan-320.png'), fullPage: true });
    await page.setViewportSize({ width: 1360, height: 1000 });

    const created = [];
    for (const [name, scenario] of [['Reference campaign', 'healthy'], ['Latest campaign', 'ignored_write']]) {
      const response = await page.request.post(`${base}/api/campaigns`, { data: { name, scenarios: [scenario], seeds: [3], plan } });
      assert.equal(response.status(), 202);
      created.push(await waitForCampaign(page, (await response.json()).id));
    }
    await page.getByRole('button', { name: 'Refresh campaigns', exact: true }).click();
    await page.getByRole('heading', { name: 'Latest campaign', exact: true }).waitFor();
    let releaseEarlier, earlierRequested;
    const earlierWait = new Promise(resolve => { earlierRequested = resolve; });
    const earlierHold = new Promise(resolve => { releaseEarlier = resolve; });
    await page.route(`**/api/campaigns/${created[0].id}`, async route => {
      const response = await route.fetch();
      earlierRequested();
      await earlierHold;
      await route.fulfill({ response });
    });
    await page.locator('.campaign-list').getByRole('button', { name: /Reference campaign/ }).click();
    await earlierWait;
    const latestResponse = page.waitForResponse(response => response.url().endsWith(`/api/campaigns/${created[1].id}`));
    await page.locator('.campaign-list').getByRole('button', { name: /Latest campaign/ }).click();
    await latestResponse;
    releaseEarlier();
    await page.unrouteAll({ behavior: 'wait' });
    await sleep(100);
    assert.equal(await page.locator('#campaign-result-heading').innerText(), 'Latest campaign');
    checks.push('Delayed campaign responses cannot replace the most recently selected saved record');

    await page.locator('.campaign-case').getByRole('button', { name: 'Inspect', exact: true }).click();
    await page.getByRole('heading', { name: 'Failure detected', exact: true }).waitFor();
    await page.waitForFunction(() => document.activeElement?.id === 'run-title');
    checks.push('Opening saved evidence moves keyboard focus to the loaded execution heading');
    for (const width of [375, 320]) {
      await page.setViewportSize({ width, height: 900 });
      await noPageOverflow(page, `Long saved test evidence at ${width}px`);
    }
    await page.screenshot({ path: path.join(shots, 'long-evidence-320.png'), fullPage: true });
    await page.getByRole('button', { name: 'Run history', exact: true }).click();
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await page.locator('.evidence-comparison > summary').click();
    await page.locator('#compare-left').selectOption(created[0].cases[0].run_id);
    await page.locator('#compare-right').selectOption(created[1].cases[0].run_id);
    await page.getByRole('button', { name: 'Compare evidence', exact: true }).click();
    await page.getByText('Conditions differ: interpret with care', { exact: true }).waitFor();
    for (const width of [375, 320]) {
      await page.setViewportSize({ width, height: 900 });
      await noPageOverflow(page, `Long saved comparison at ${width}px`);
    }
    await page.screenshot({ path: path.join(shots, 'long-comparison-320.png'), fullPage: true });
    checks.push('Valid maximum-length names and identifiers wrap in plan preview, evidence and comparison at 375px and 320px');

    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.evaluate(() => {
      const probe = document.createElement('span'); probe.id = 'motion-probe'; probe.className = 'spin'; document.body.appendChild(probe);
    });
    assert.ok(await page.locator('#motion-probe').evaluate(el => parseFloat(getComputedStyle(el).animationDuration) <= 0.001));
    await page.locator('#motion-probe').evaluate(el => el.remove());
    await page.setViewportSize({ width: 1360, height: 1000 });
    await page.getByRole('button', { name: 'Switch to dark appearance', exact: true }).click();
    await page.screenshot({ path: path.join(shots, 'comparison-dark.png'), fullPage: true });
    checks.push('Reduced-motion preference suppresses continuous spinner motion and dark appearance preserves the review state');

    await page.getByRole('button', { name: 'Campaigns', exact: true }).click();
    await page.getByRole('button', { name: 'Standard catalog', exact: true }).click();
    await page.locator('#campaign-name').fill('Cross-view worker check');
    await seeds.fill('1, 2, 3, 4, 5');
    for (const checkbox of await page.locator('.scenario-choices input').all()) await checkbox.check();
    let releaseCreation, creationRequested;
    const creationWait = new Promise(resolve => { creationRequested = resolve; });
    const creationHold = new Promise(resolve => { releaseCreation = resolve; });
    await page.route('**/api/campaigns', async route => {
      if (route.request().method() !== 'POST') { await route.continue(); return; }
      const response = await route.fetch();
      creationRequested(await response.json());
      await creationHold;
      await route.fulfill({ response });
    });
    await page.getByRole('button', { name: 'Start campaign', exact: true }).click();
    const pending = await creationWait;
    await page.getByRole('button', { name: 'Test bench', exact: true }).click();
    assert.equal(await page.locator('.run-config button[type="submit"]').isDisabled(), true);
    await page.getByRole('button', { name: 'Ordering lab', exact: true }).click();
    assert.equal(await page.getByRole('button', { name: 'Train and evaluate', exact: true }).isDisabled(), true);
    releaseCreation();
    await page.unrouteAll({ behavior: 'wait' });
    const finished = await waitForCampaign(page, pending.id);
    assert.equal(finished.completed, 25);
    await page.getByRole('button', { name: 'Test bench', exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('.run-config button[type="submit"]')?.disabled);
    await page.getByRole('button', { name: 'Run history', exact: true }).click();
    await page.getByText('Latest 27 saved runs shown.', { exact: false }).waitFor();
    checks.push('A pending campaign blocks competing work across views; hidden campaign polling releases the worker and refreshes all 25 completed runs');

    for (const theme of ['light', 'dark']) {
      if (await page.evaluate(() => document.documentElement.dataset.theme) !== theme) await page.getByRole('button', { name: `Switch to ${theme} appearance`, exact: true }).click();
      await page.getByRole('button', { name: 'Run history', exact: true }).click();
      await readableText(page, ['.history-table th:not(:last-child)']);
      await page.locator('.history-table tbody tr').filter({ hasText: 'Healthy device' }).first().locator('.history-id').click();
      await page.getByRole('heading', { name: 'All checks passed', exact: true }).waitFor();
      await readableText(page, ['.subtitle', '.result-context', '.run-reading-guide', '#scenario-help', '#seed-help', '#order-help', '.test-row.selected .sequence-index', '.test-row.selected .test-row-name > span', '.trace-index', '.trace-response > span', '.trace-response > pre', '.run-config label', '.run-config .button-primary']);
      await page.locator('.test-row:not(.selected)').first().hover();
      await page.waitForTimeout(200);
      await readableText(page, ['.test-row:hover .test-row-status.status-pass']);
      // Check the shared error-text style without inventing a runner-error execution.
      await page.locator('.test-row:hover').evaluate(row => {
        const fixture = document.createElement('span'); fixture.className = 'test-row-status status-error';
        fixture.dataset.contrastFixture = 'true'; fixture.textContent = 'Style fixture'; row.appendChild(fixture);
      });
      await readableText(page, ['.test-row:hover [data-contrast-fixture]']);
      await page.locator('[data-contrast-fixture]').evaluate(element => element.remove());
      await page.getByRole('button', { name: 'Campaigns', exact: true }).click();
      await seeds.fill('3, 3');
      await page.getByRole('button', { name: 'Custom JSON plan', exact: true }).click();
      await page.locator('#plan-json').fill('{');
      await page.getByRole('button', { name: 'Validate plan', exact: true }).click();
      await page.getByRole('heading', { name: 'Plan needs attention', exact: true }).waitFor();
      await readableText(page, ['.campaign-form-grid label', '#campaign-name', '#campaign-seeds', '#seeds-help', '#seeds-error', '#plan-error', '.saved-campaigns-heading .button']);
      await page.screenshot({ path: path.join(shots, `contrast-${theme}.png`), fullPage: true });
    }
    checks.push('Targeted light/dark text, labels, errors, buttons and hovered PASS status meet 4.5:1 computed contrast; shared runner-error hover style is checked with a temporary DOM style fixture');
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(root, 'docs/results/browser-accessibility-check.json'), JSON.stringify({
      checked_at: new Date().toISOString(), status: 'passed', checks, browser: await browser.version(),
      viewport: [1360, 1000], mobile_widths: [375, 320], context: 'Actual API and production frontend; isolated local runtime; delayed saved-record response to verify latest selection wins',
    }, null, 2));
    console.log(JSON.stringify({ status: 'passed', checks, screenshots: shots }, null, 2));
  } finally {
    if (browser) await browser.close();
    if (server && server.exitCode === null && server.signalCode === null) {
      const exited = new Promise(resolve => server.once('exit', resolve));
      server.kill();
      await Promise.race([exited, sleep(5000)]);
    }
    fs.closeSync(log);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
