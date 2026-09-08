// Browser integration against an owned Lab. No model calls; --live explicitly
// runs a scripted reference on real local Studio. Never print credentials.
// --report=RUN_ID checks a saved report without creating another run.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');

const dataDir = path.resolve(process.env.LAB_DATA_DIR || path.join(os.homedir(), '.genlayer-agent-lab'));
const baseURL = process.env.LAB_URL || 'http://127.0.0.1:8765';
const adminToken = fs.readFileSync(path.join(dataDir, 'admin.token'), 'utf8').trim();
const live = process.argv.includes('--live');
const unsafe = process.argv.includes('--unsafe');
const reportRunId = process.argv.find(arg => arg.startsWith('--report='))?.slice('--report='.length);
const output = path.resolve(process.env.LAB_BROWSER_OUTPUT || path.join(dataDir, 'usability'));
fs.mkdirSync(output, {recursive:true});
let testToken = '';
const redact = value => [adminToken, testToken].filter(Boolean).reduce((text, secret) => text.replaceAll(secret, '[redacted]'), String(value));

async function reference(run) {
  const python = process.env.LAB_PYTHON || path.resolve(process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
  const program = [
    'import os,json',
    'from genlayer_agent_lab.client import LabClient',
    'from genlayer_agent_lab.project_reference import run_project_agent',
    'with LabClient(os.environ["LAB_URL"],os.environ["LAB_TOKEN"]) as client:',
    ' result=run_project_agent(client,os.environ["LAB_RUN_ID"],unsafe=os.environ["LAB_UNSAFE"]=="1",timeout_seconds=900)',
    ' print(json.dumps(result))',
  ].join('\n');
  return new Promise((resolve, reject) => {
    const child = spawn(python, ['-c', program], {windowsHide:true, env:{...process.env,LAB_URL:baseURL,LAB_TOKEN:run.agent_token,LAB_RUN_ID:run.run_id,LAB_UNSAFE:unsafe ? '1' : '0'}, stdio:['ignore','pipe','pipe']});
    let text = '';
    child.stdout.on('data', bytes => { text = (text + bytes).slice(-8000); });
    child.stderr.on('data', bytes => { text = (text + bytes).slice(-8000); });
    child.on('error', error => reject(new Error(redact(error.message))));
    child.on('exit', code => code === 0 ? resolve() : reject(new Error(redact(text))));
  });
}

(async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.LAB_BROWSER_CHANNEL ? {channel:process.env.LAB_BROWSER_CHANNEL} : {})});
  let created;
  const checks = {};
  try {
    const page = await browser.newPage({viewport:{width:1280,height:900}});
    const errors = [];
    page.on('pageerror', error => errors.push(redact(error.message)));
    page.on('request', request => { assert(!request.url().includes(adminToken), 'Administrator token reached an HTTP URL'); });
    await page.goto(baseURL);
    await page.locator('#auth').waitFor({state:'visible'});
    await page.screenshot({path:path.join(output,'welcome-desktop.png'),fullPage:true});
    // Setup opens a document with a fragment; navigating only the fragment of
    // an already loaded document would not execute its startup script again.
    await page.goto('about:blank');
    await page.goto(baseURL + '/#token=' + encodeURIComponent(adminToken));
    await page.locator('#workspace').waitFor({state:'visible'});
    assert(!page.url().includes(adminToken));
    await page.waitForFunction(() => document.querySelectorAll('.template-card').length === 11);
    checks.sign_in_and_templates = 'pass';
    await page.screenshot({path:path.join(output,'templates-desktop.png'),fullPage:true});
    await page.locator('#template-filter').selectOption('investigation');
    assert.equal(await page.locator('.template-card').count(), 6);
    await page.locator('#template-filter').selectOption('all');
    await page.locator('[data-template="prediction-finalize"]').click();
    await page.locator('#field-title').fill('Guided agent trial' + (unsafe ? ' — faulty control' : ' — safe control'));
    await page.locator('#prepare-review').click();
    await page.locator('#review-panel').waitFor({state:'visible'});
    assert((await page.locator('#review-rules').textContent()).length > 30);
    const initialSpec = JSON.parse(await page.locator('#spec').inputValue());
    await page.locator('#field-title').fill('Changed after review');
    await page.locator('#review-panel').waitFor({state:'hidden'});
    assert.equal(await page.locator('#review-confirmed').isChecked(), false);
    checks.stale_review_invalidated = 'pass';

    // Imported exact integers must survive UI -> preview -> review without rounding.
    const exact = '1000000000000000000000000001';
    initialSpec.policy.max_total_fee = {$lab_integer:exact};
    initialSpec.title = '<img src=x onerror="window.labInjected=true">';
    await page.locator('.advanced-authoring').evaluate(el => { el.open = true; });
    await page.locator('#spec').fill(JSON.stringify(initialSpec));
    await page.locator('#advanced-form button').click();
    await page.locator('#review-panel').waitFor({state:'visible'});
    const imported = JSON.parse(await page.locator('#spec').inputValue());
    assert.equal(imported.policy.max_total_fee.$lab_integer, exact);
    assert.equal(await page.evaluate(() => Boolean(window.labInjected)), false);
    checks.import_exact_integer_and_text_safety = 'pass';

    await page.locator('[data-template="prediction-finalize"]').click();
    await page.locator('#field-title').fill('Guided agent trial' + (unsafe ? ' — faulty control' : ' — safe control'));
    await page.locator('#prepare-review').click();
    await page.locator('#review-panel').waitFor({state:'visible'});
    await page.locator('#review-confirmed').check();
    await page.locator('#review-panel').screenshot({path:path.join(output,'review-desktop.png')});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.locator('#review-panel').screenshot({path:path.join(output,'review-mobile.png')});
    await page.setViewportSize({width:1280,height:900});
    checks.responsive_review = 'pass';

    if (live || reportRunId) {
      if (live) {
      await page.waitForFunction(() => !document.querySelector('#create').disabled, null, {timeout:60000});
      const [response] = await Promise.all([
        page.waitForResponse(response => response.url().endsWith('/v1/workflows') && response.request().method() === 'POST', {timeout:60000}),
        page.locator('#create').click(),
      ]);
      assert.equal(response.status(), 201, 'Guided run creation failed');
      created = await response.json(); testToken = created.agent_token;
      await page.locator('#connection').waitFor({state:'visible'});
      const settings = JSON.parse(await page.locator('#credential').inputValue());
      const entry = settings.mcpServers['genlayer-lab'];
      assert.equal(entry.env.LAB_URL, baseURL);
      assert.equal(entry.env.LAB_MODE, 'workflow');
      assert.equal(entry.env.LAB_TOKEN, testToken);
      assert(!JSON.stringify(settings).includes(adminToken));
      await page.locator('#check-connection').click();
      assert(!/contacted|connected/i.test(await page.locator('#connection-badge').textContent()));
      // A desktop connector talking through an SSH tunnel uses its own executable.
      await page.locator('#agent-location').selectOption('tunnel');
      await page.locator('#agent-url').fill('http://127.0.0.1:8875');
      await page.locator('#mcp-path').fill('/home/example/.local/bin/gl-agent-lab-mcp');
      const tunnel = JSON.parse(await page.locator('#credential').inputValue());
      assert.equal(tunnel.mcpServers['genlayer-lab'].env.LAB_URL, 'http://127.0.0.1:8875');
      assert.equal(tunnel.mcpServers['genlayer-lab'].command, '/home/example/.local/bin/gl-agent-lab-mcp');
      await page.locator('#agent-location').selectOption('same');
      checks.connection_settings_and_no_false_connected = 'pass';
      // Use the generated run token through the actual agent API. This is a
      // scripted browser integration control, not a real model trial.
      await reference(created);
      await page.locator('#check-connection').click();
      } else {
        await page.locator('.run-card').filter({hasText:reportRunId}).click();
      }
      await page.locator('#refresh').click();
      await page.waitForFunction(() => document.querySelector('#evaluation-cards').children.length > 0, null, {timeout:60000});
      const result = JSON.parse(await page.locator('#evidence').textContent());
      assert.equal(result.verification, unsafe ? 'fail' : 'pass');
      assert.equal(result.cleanup, 'restored');
      assert.equal(result.run_id, created?.run_id || reportRunId);
      checks[live ? 'actual_studio_reference' : 'saved_studio_report'] = {run_id:result.run_id,verification:result.verification,cleanup:result.cleanup,...(live ? {agent:'scripted ' + (unsafe ? 'faulty' : 'safe')} : {})};
      const downloadPromise = page.waitForEvent('download');
      await page.locator('#download-readable').click();
      const download = await downloadPromise;
      await download.saveAs(path.join(output, 'readable-' + (unsafe ? 'fail' : 'pass') + '.html'));
      const exported = fs.readFileSync(path.join(output, 'readable-' + (unsafe ? 'fail' : 'pass') + '.html'), 'utf8');
      assert([adminToken,testToken].filter(Boolean).every(secret => !exported.includes(secret)));
      assert(exported.includes('What happened'));
      await page.locator('#detail').screenshot({path:path.join(output,'result-' + (unsafe ? 'fail' : 'pass') + '-desktop.png')});
      await page.setViewportSize({width:390,height:844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.locator('#detail').screenshot({path:path.join(output,'result-' + (unsafe ? 'fail' : 'pass') + '-mobile.png')});
      checks.readable_export = 'pass';
      fs.writeFileSync(path.join(output,'report-' + (unsafe ? 'fail' : 'pass') + '.json'), JSON.stringify(result,null,2));
    }
    assert.deepEqual(errors, []);
    checks.page_errors = [];
    fs.writeFileSync(path.join(output,'browser-' + (live ? unsafe ? 'live-faulty' : 'live-safe' : reportRunId ? unsafe ? 'report-fail' : 'report-pass' : 'authoring') + '.json'), JSON.stringify(checks,null,2));
    console.log(JSON.stringify(checks));
  } finally {
    if (created) {
      const response = await fetch(baseURL + '/v1/workflows/' + created.run_id, {headers:{Authorization:'Bearer ' + adminToken}});
      const run = await response.json();
      if (!['completed','cancelled','interrupted','inconclusive','failed'].includes(run.status)) await fetch(baseURL + '/v1/workflows/' + created.run_id + '/cancel', {method:'POST',headers:{Authorization:'Bearer ' + adminToken}});
    }
    await browser.close();
  }
})().catch(error => { console.error(redact(error.stack || error.message)); process.exitCode = 1; });
