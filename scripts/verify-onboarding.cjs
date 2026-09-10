// Browser integration against an owned Lab. No model calls; --live explicitly
// runs a scripted reference on real local Studio. Never print credentials.
// --report=RUN_ID checks a saved report without creating another run.
// --connection-fixture checks connection UI in a browser with fully intercepted
// local assets/API fixtures. It uses synthetic credentials and no running Lab.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');

const connectionFixture = process.argv.includes('--connection-fixture');
const dataDir = path.resolve(process.env.LAB_DATA_DIR || path.join(os.homedir(), '.genlayer-agent-lab'));
const baseURL = connectionFixture ? 'http://127.0.0.1:8999' : process.env.LAB_URL || 'http://127.0.0.1:8765';
const adminToken = connectionFixture ? 'fixture-workspace-secret' : fs.readFileSync(path.join(dataDir, 'admin.token'), 'utf8').trim();
const live = process.argv.includes('--live');
const unsafe = process.argv.includes('--unsafe');
const reportRunId = process.argv.find(arg => arg.startsWith('--report='))?.slice('--report='.length);
const output = path.resolve(process.env.LAB_BROWSER_OUTPUT || path.join(dataDir, 'usability'));
if (!connectionFixture) fs.mkdirSync(output, {recursive:true});
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

async function verifyConnectionFixture(browser, checks) {
  const fixtureToken = 'fixture-run-secret';
  const runId = 'fixture-reviewed-run';
  const spec = {title:'Connection fixture',task:'Read the public task.',timeout_seconds:1800,
    project_snapshot:{definition:{contracts:{example:{}}}},fixtures:{},context:{},evidence:[],
    expectations:{rules:[{label:'Private fixture canary',op:'exists'}],required_actions:[],forbidden_actions:[],require_finalized:true},
    policy:{allow_appeal:false,operations:{},max_fee:null,max_total_fee:null}};
  let creations = 0, status = 'running';
  const page = await browser.newPage({viewport:{width:1280,height:900}});
  const errors = [], unexpected = [];
  page.on('pageerror', error => errors.push(redact(error.message)));
  await page.addInitScript(() => {
    window.fixtureClipboard = {value:'',deny:false};
    Object.defineProperty(navigator, 'clipboard', {value:{writeText:async value => {
      if (window.fixtureClipboard.deny) throw new Error('Clipboard denied');
      window.fixtureClipboard.value = value;
    }}});
  });
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    assert(!url.href.includes(adminToken) && !url.href.includes(fixtureToken));
    const asset = {'/':'workflows.html','/assets/workflows.js':'workflows.js','/assets/styles.css':'styles.css','/assets/onboarding.css':'onboarding.css'}[url.pathname];
    if (asset) return route.fulfill({path:path.resolve('src/genlayer_agent_lab/assets', asset),contentType:asset.endsWith('.js') ? 'text/javascript' : asset.endsWith('.css') ? 'text/css' : 'text/html'});
    let result;
    if (url.pathname === '/v1/onboarding/templates') result = {templates:[{id:'prediction-finalize',title:'Connection fixture',description:'Synthetic browser check',fields:[{name:'timeout_seconds',type:'number',default:600}]}]};
    else if (url.pathname === '/v1/onboarding/status') result = {ready:true,checks:[],server_url:baseURL,mcp_command:'/opt/agent-lab/bin/gl-agent-lab-mcp'};
    else if (url.pathname === '/v1/onboarding/draft') {
      assert.equal(request.postDataJSON().values.timeout_seconds, 1800);
      result = {spec,digest:'fixture-reviewed-digest',summary:[],rules:[],warnings:[]};
    } else if (url.pathname === '/v1/onboarding/review') result = {spec};
    else if (url.pathname === '/v1/workflows' && request.method() === 'POST') {
      creations++; result = {run_id:runId,agent_token:fixtureToken,status:'preparing'};
    } else if (url.pathname === '/v1/workflows') result = creations ? [{run_id:runId,title:spec.title,status}] : [];
    else if (url.pathname === '/v1/workflows/' + runId || url.pathname === '/v1/workflows/' + runId + '/report') result = {run_id:runId,title:spec.title,status,profile:'project',operations:[],checks:[],verification:'inconclusive'};
    else if (url.pathname === '/v1/onboarding/connection/' + runId) result = {connected:false,detail:'No authenticated agent activity observed yet.'};
    else { unexpected.push(request.method() + ' ' + url.pathname); return route.abort(); }
    return route.fulfill({json:result,status:url.pathname === '/v1/workflows' && request.method() === 'POST' ? 201 : 200});
  });
  async function createFixture() {
    await page.goto(baseURL + '/#token=' + adminToken);
    await page.locator('#workspace').waitFor({state:'visible'});
    await page.locator('[data-template="prediction-finalize"]').click();
    assert.equal(await page.locator('#field-timeout_seconds').inputValue(), '30');
    await page.locator('#prepare-review').click();
    await page.locator('#review-panel').waitFor({state:'visible'});
    await page.locator('#review-confirmed').check();
    await page.locator('#create').click();
    await page.locator('#connection').waitFor({state:'visible'});
    await page.waitForFunction(() => !document.querySelector('#copy-setup-prompt').disabled);
  }
  async function copiedPrompt() {
    await page.locator('#copy-setup-prompt').click();
    return page.evaluate(() => window.fixtureClipboard.value);
  }
  await createFixture();
  assert.equal(await page.locator('#setup-prompt').inputValue(), '');
  let prompt = await copiedPrompt();
  const settings = JSON.parse(await page.locator('#credential').inputValue());
  assert(prompt.includes(JSON.stringify(settings, null, 2)));
  assert(prompt.includes(fixtureToken) && !prompt.includes(adminToken));
  assert(!prompt.includes('Private fixture canary'));
  assert.match(prompt, /stdio MCP connector/);
  assert.match(prompt, /actual running agent session or gateway/);
  assert.match(prompt, /mcp\.servers/);
  assert.match(prompt, /actual observe tool/);
  assert.match(prompt, /do not create another run/);
  assert.match(prompt, /invoke finish only after/);
  assert.equal(await page.locator('#setup-prompt').inputValue(), '');
  assert.equal(await page.locator('#setup-prompt-details').evaluate(el => el.open), false);
  assert(!await page.locator('#feedback').textContent().then(value => value.includes(fixtureToken)));
  assert.equal(await page.evaluate(secret => [localStorage,sessionStorage].some(storage => Object.values(storage).some(value => value.includes(secret))), fixtureToken), false);
  checks.setup_prompt_current_run_and_private_review_separation = 'pass';

  await page.locator('#setup-prompt-details summary').click();
  await page.waitForFunction(() => document.querySelector('#setup-prompt').value.length > 0);
  assert.equal(await page.locator('#setup-prompt').inputValue(), prompt);
  assert.equal(await page.locator('#setup-prompt').getAttribute('readonly'), '');
  await page.locator('#agent-location').selectOption('tunnel');
  assert.equal(await page.locator('#setup-prompt').inputValue(), '');
  assert.equal(await page.locator('#copy-setup-prompt').isDisabled(), true);
  await page.locator('#agent-url').fill('http://127.0.0.1:8875');
  await page.locator('#mcp-path').fill('gl-agent-lab-mcp');
  assert.equal(await page.locator('#copy-setup-prompt').isDisabled(), true);
  await page.locator('#mcp-path').fill('C:\\Agent Tools\\gl-agent-lab-mcp.exe');
  prompt = await copiedPrompt();
  assert(prompt.includes('C:\\\\Agent Tools\\\\gl-agent-lab-mcp.exe') && prompt.includes('http://127.0.0.1:8875'));
  await page.locator('#agent-url').fill('https://example.com');
  assert.equal(await page.locator('#copy-setup-prompt').isDisabled(), true);
  assert.equal(await page.locator('#credential').inputValue(), '');
  await page.locator('#agent-url').fill('http://127.0.0.1:8875');
  for (const [client, marker] of [['python','workflow_observe'],['typescript','workflowObserve'],['http','POST /v1/workflows/' + runId + '/observe']]) {
    await page.locator('#agent-client').selectOption(client);
    prompt = await copiedPrompt();
    assert(prompt.includes(await page.locator('#credential').inputValue()));
    assert(prompt.includes(marker) && prompt.includes(fixtureToken));
    assert(!prompt.includes('mcpServers'));
  }
  checks.selected_client_tunnel_path_and_invalid_settings = 'pass';

  await page.evaluate(() => { window.fixtureClipboard.deny = true; });
  await page.locator('#copy-setup-prompt').click();
  await page.waitForFunction(() => document.querySelector('#setup-prompt-details').open);
  assert.equal(await page.locator('#setup-prompt').inputValue(), prompt);
  assert.equal(await page.locator('#setup-prompt').evaluate(el => el.selectionStart === 0 && el.selectionEnd === el.value.length && document.activeElement === el), true);
  assert(!(await page.locator('#feedback').textContent()).includes(fixtureToken));
  assert(!(await page.locator('#notice').textContent()).includes(fixtureToken));
  checks.clipboard_denial_uses_selected_readonly_prompt = 'pass';
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.setViewportSize({width:1280,height:900});

  await page.locator('#new-test').click();
  assert.equal(await page.locator('#setup-prompt').inputValue(), '');
  assert.equal(await page.locator('#copy-setup-prompt').isDisabled(), true);
  await page.locator('.run-card').filter({hasText:runId}).click();
  await page.locator('#connection').waitFor({state:'visible'});
  await page.locator('#copy-setup-prompt').click();
  await page.waitForFunction(() => document.querySelector('#setup-prompt').value.length > 0);
  assert.equal(creations, 1, 'Connection actions created another test');
  status = 'inconclusive';
  await page.locator('#refresh').click();
  await page.locator('#connection').waitFor({state:'hidden'});
  assert.equal(await page.locator('#setup-prompt').inputValue(), '');
  assert.equal(await page.locator('#credential').inputValue(), '');
  assert.equal(await page.locator('#copy-setup-prompt').isDisabled(), true);
  checks.run_change_and_ended_credentials_cleared_without_creation = 'pass';

  status = 'running';
  await page.goto('about:blank');
  await createFixture();
  await page.locator('#setup-prompt-details summary').click();
  await page.waitForFunction(() => document.querySelector('#setup-prompt').value.length > 0);
  await page.evaluate(() => { const expiredTime = Date.now() + 1801000; Date.now = () => expiredTime; });
  await page.locator('#copy-setup-prompt').click();
  await page.locator('#connection').waitFor({state:'hidden'});
  assert.equal(await page.locator('#setup-prompt').inputValue(), '');
  assert.equal(await page.locator('#credential').inputValue(), '');
  assert.equal(await page.locator('#copy-setup-prompt').isDisabled(), true);
  checks.expired_credentials_cannot_be_copied = 'pass';

  await page.goto('about:blank');
  await createFixture();
  await page.locator('#setup-prompt-details summary').click();
  await page.waitForFunction(() => document.querySelector('#setup-prompt').value.length > 0);
  await page.locator('#disconnect').click();
  await page.locator('#auth').waitFor({state:'visible'});
  assert.equal(await page.locator('#setup-prompt').inputValue(), '');
  assert.equal(await page.locator('#credential').inputValue(), '');
  assert.equal(await page.evaluate(() => sessionStorage.length), 0);
  checks.lock_clears_prompt_and_workspace_key = 'pass';
  assert.deepEqual(errors, []);
  assert.deepEqual(unexpected, []);
  checks.live_requests = 0;
  await page.close();
}

(async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.LAB_BROWSER_CHANNEL ? {channel:process.env.LAB_BROWSER_CHANNEL} : {})});
  let created;
  const checks = {};
  try {
    if (connectionFixture) {
      await verifyConnectionFixture(browser, checks);
      console.log(JSON.stringify(checks));
      return;
    }
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
