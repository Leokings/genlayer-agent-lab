// Unified dashboard browser regression. Every HTTP request is intercepted;
// synthetic runs only, with no Lab, Docker, Studio, model or external calls.
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('playwright');

(async () => {
  const origin = 'http://127.0.0.1:8999', owner = 'fixture-owner-key';
  const privateCanary = 'PRIVATE_EXPECTATION_DO_NOT_COPY';
  const errors = [], unexpected = [], requests = [], checks = {};
  let catalogFails = true, previewFails = false, creates = 0, selectedAgent = 'external';
  let holdPreview = false, pendingPreview, previewHeldReady;
  const catalog = {scenarios:[{id:'escrow-normal',title:'Escrow: normal',description:'A final approval',task:'Read the public task',timeout_seconds:600},{id:'escrow-provisional',title:'Escrow: provisional',description:'A delayed decision',task:'Read the public task',timeout_seconds:600}],bindings:[{id:'custom-binding',title:'Imported custom binding'}],server_url:'http://127.0.0.1:8765',mcp_command:'/opt/lab/bin/gl-agent-lab-mcp',checks:[{id:'glsim',label:'GLSim package',status:'pass',detail:'Package found; execution not checked'}],runtime_verified:false};
  const workflow = {run_id:'project-history',title:'Studio saved test',status:'completed',profile:'project',verification:'pass',operations:[],checks:[],created_at:1};
  const reports = new Map();
  function makeReport(id, backend, agent = 'external', status = 'completed') {
    return {run_id:id,scenario:'escrow-normal',agent,status,verdict:status === 'completed' ? 'pass' : 'inconclusive',created_at:2,grades:{behavior:{status:status === 'completed' ? 'pass' : 'inconclusive',detail:'No rejected action attempts'}},findings:[],events:[{sequence:0,type:'created',tick:0,data:{}}],manifest:{backend,scenario:{title:'Saved scenario',timeout_seconds:600,expectation:privateCanary},units:'simulated test units'}};
  }
  reports.set('fixture-history', makeReport('fixture-history','fixture'));
  reports.set('studio-scenario-history', makeReport('studio-scenario-history','studio'));
  const browser = await chromium.launch({headless:true,...(process.env.LAB_BROWSER_CHANNEL ? {channel:process.env.LAB_BROWSER_CHANNEL} : {})});
  const page = await browser.newPage({viewport:{width:1280,height:900}});
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.fixtureClipboard = {value:'',deny:false};
    Object.defineProperty(navigator, 'clipboard', {value:{writeText:async value => {
      if (window.fixtureClipboard.deny) throw new Error('Clipboard denied');
      window.fixtureClipboard.value = value;
    }}});
  });
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url());
    assert(!url.href.includes(owner) && !url.href.includes('fixture-run-key-'));
    requests.push(req.method() + ' ' + url.pathname);
    if (url.origin !== origin) { unexpected.push(url.origin); return route.abort(); }
    const asset = {'/':'workflows.html','/assets/workflows.html':'workflows.html','/assets/index.html':'index.html','/assets/dashboard-redirect.js':'dashboard-redirect.js','/assets/quick-tests.js':'quick-tests.js','/assets/workflows.js':'workflows.js','/assets/styles.css':'styles.css','/assets/onboarding.css':'onboarding.css'}[url.pathname];
    if (asset) return route.fulfill({path:path.resolve('src/genlayer_agent_lab/assets',asset),contentType:asset.endsWith('.js') ? 'text/javascript' : asset.endsWith('.css') ? 'text/css' : 'text/html'});
    assert.equal(req.headers().authorization, 'Bearer ' + owner);
    if (url.pathname === '/v1/onboarding/templates') return route.fulfill({json:{templates:[]}});
    if (url.pathname === '/v1/onboarding/status') return route.fulfill({json:{ready:false,checks:[{id:'studio',label:'Studio',status:'fail',detail:'Not installed'}],server_url:origin,mcp_command:'/fixture/connector'}});
    if (url.pathname === '/v1/workflows') return route.fulfill({json:[workflow]});
    if (url.pathname === '/v1/workflows/project-history' || url.pathname === '/v1/workflows/project-history/report') return route.fulfill({json:workflow});
    if (url.pathname === '/v1/onboarding/connection/project-history') return route.fulfill({json:{connected:false,detail:'Historical test is complete.'}});
    if (url.pathname === '/v1/runs' && req.method() === 'GET') return route.fulfill({json:Array.from(reports.values(), value => ({run_id:value.run_id,scenario_id:value.scenario,title:value.run_id,status:value.status,backend:value.manifest.backend,agent:value.agent,created_at:value.created_at}))});
    if (url.pathname === '/v1/quick-tests/catalog') return route.fulfill(catalogFails ? {status:404,json:{detail:'Quick catalog is unavailable'}} : {json:catalog});
    if (url.pathname === '/v1/quick-tests/preview') {
      const input = req.postDataJSON(); selectedAgent = input.agent;
      const value = {selection:{...input,backend:input.binding_id ? 'container-glsim' : 'glsim',timeout_seconds:600},digest:'a'.repeat(64),summary:['Scenario: ' + input.scenario_id,'Selected agent: ' + input.agent,'Expected private condition: ' + privateCanary],warnings:['Scripted timeline; no actual chain appeal.']};
      if (holdPreview) { await new Promise(resolve => { pendingPreview = {route,resolve,value}; previewHeldReady(); }); return; }
      return route.fulfill(previewFails ? {status:400,json:{detail:'Unsupported selection <img src=x onerror="window.injected=true"> ' + owner}} : {json:value});
    }
    if (url.pathname === '/v1/quick-tests/runs') {
      const input = req.postDataJSON();
      assert.equal(input.expected_sha256, 'a'.repeat(64)); assert(input.reviewer.trim());
      assert.equal(input.agent, selectedAgent); assert(!('backend' in input));
      const id = 'quick-created-' + ++creates;
      const backend = input.binding_id ? 'container-glsim' : 'glsim';
      reports.set(id, makeReport(id,backend,input.agent,'running'));
      return route.fulfill({status:201,json:{run_id:id,agent_token:'fixture-run-key-' + creates,status:'queued',selection:{...input,backend}}});
    }
    const match = url.pathname.match(/^\/v1\/runs\/([^/]+)\/(report|cancel)$/);
    if (match && reports.has(match[1])) {
      const report = reports.get(match[1]);
      if (match[2] === 'cancel') { report.status = 'cancelled'; return route.fulfill({json:{run_id:report.run_id,status:'cancelled'}}); }
      const format = url.searchParams.get('format');
      if (format === 'html') return route.fulfill({body:'<!doctype html><p>Readable synthetic report</p>',contentType:'text/html'});
      if (format === 'junit') return route.fulfill({body:'<testsuite name="quick-simulation"/>',contentType:'application/xml'});
      return route.fulfill({json:report});
    }
    unexpected.push(req.method() + ' ' + url.pathname); return route.abort();
  });
  const copied = async () => { await page.locator('#quick-copy-prompt').click(); return page.evaluate(() => window.fixtureClipboard.value); };
  const chooseHistory = id => page.locator(`[data-run-id="${id}"]`).click();
  async function reviewCreate(agent = 'external') {
    await page.locator('#quick-agent').selectOption(agent);
    await page.locator('#quick-preview').click();
    await page.locator('#quick-review').waitFor({state:'visible'});
    assert.equal(await page.locator('#quick-create').isDisabled(), true);
    await page.locator('#quick-review-confirmed').check();
    const expectedId = 'quick-created-' + (creates + 1);
    await page.locator('#quick-create').click();
    await page.waitForFunction(id => document.querySelector('#quick-meta').textContent.includes(id), expectedId);
    await page.waitForFunction(() => !document.querySelector('#quick-create').textContent.includes('Creating'));
  }
  try {
    await page.goto(origin + '/#token=' + owner);
    await page.locator('#workspace').waitFor({state:'visible'});
    assert.equal(await page.locator('#test-mode').inputValue(), 'studio');
    await page.locator('[data-run-id="fixture-history"]').waitFor();
    assert.match(await page.locator('#runs').innerText(), /Studio workflow/);
    assert.match(await page.locator('#runs').innerText(), /Fixture-only scenario/);
    assert.match(await page.locator('#runs').innerText(), /Earlier Studio scenario/);
    await chooseHistory('fixture-history'); await page.locator('#quick-detail').waitFor({state:'visible'});
    await page.waitForFunction(() => document.querySelector('#quick-engine-note').textContent.includes('Historical fixture replay'));
    await chooseHistory('studio-scenario-history');
    await page.waitForFunction(() => document.querySelector('#quick-engine-note').textContent.includes('Historical Studio scenario'));
    await chooseHistory('project-history'); await page.locator('#detail').waitFor({state:'visible'});
    assert.equal(await page.locator('#quick-detail').isVisible(), false);
    assert(requests.includes('GET /v1/runs/fixture-history/report') && requests.includes('GET /v1/workflows/project-history/report'));
    checks.unified_history_routes_workflows_and_legacy_scenarios_truthfully = 'pass';

    await page.locator('#test-mode').selectOption('glsim');
    await page.locator('#quick-builder-error').waitFor({state:'visible'});
    assert.equal(await page.locator('#quick-preview').isDisabled(), true);
    catalogFails = false;
    await page.locator('#quick-catalog-retry').click();
    await page.waitForFunction(() => !document.querySelector('#quick-preview').disabled);
    assert.equal(await page.locator('#studio-tests').isVisible(), false);
    assert.equal(await page.locator('#quick-agent').inputValue(), 'external');
    assert.equal(await page.locator('#quick-scenario option').count(), 2);
    assert(!(await page.locator('#quick-scenario').innerText()).includes('appeal'));
    assert.match(await page.locator('#quick-agent').innerText(), /Scripted reference/);
    checks.quick_catalog_retry_and_external_default_without_studio = 'pass';

    await page.locator('#quick-create-form').evaluate(form => form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
    assert.equal(creates, 0);
    previewFails = true; await page.locator('#quick-preview').click();
    await page.locator('#quick-builder-error').waitFor({state:'visible'});
    const errorText = await page.locator('#quick-builder-error').innerText();
    assert(errorText.includes('[redacted]') && !errorText.includes(owner) && errorText.includes('<img'));
    assert.equal(await page.locator('#quick-builder-error img').count(), 0);
    previewFails = false;
    await reviewCreate();
    await page.waitForFunction(() => !document.querySelector('#quick-copy-prompt').disabled);
    let prompt = await copied();
    assert(prompt.includes('fixture-run-key-1') && prompt.includes('quick-created-1'));
    assert(!prompt.includes(owner) && !prompt.includes(privateCanary));
    assert(prompt.includes('"LAB_MODE": "scenario"') && prompt.includes('"LAB_ROLE": "agent"'));
    assert(prompt.includes('request_decision') && prompt.includes('read_decision') && prompt.includes('There is no appeal tool'));
    assert(!prompt.includes('appeal_decision') && !prompt.includes('/v1/workflows/'));
    assert.match(await page.locator('#quick-timing').innerText(), /no separate setup allowance/);
    assert.equal(await page.locator('#quick-setup-prompt').inputValue(), '');
    assert.equal(await page.evaluate(() => [...Object.values(localStorage),...Object.values(sessionStorage)].some(value => value.includes('fixture-run-key-'))), false);
    checks.review_before_create_and_private_scenario_mode_prompt = 'pass';

    await page.locator('#quick-location').selectOption('tunnel');
    assert.equal(await page.locator('#quick-copy-prompt').isDisabled(), true);
    await page.locator('#quick-agent-url').fill('https://external.example');
    await page.locator('#quick-mcp-path').fill('/opt/agent/gl-agent-lab-mcp');
    assert.equal(await page.locator('#quick-copy-prompt').isDisabled(), true);
    await page.locator('#quick-agent-url').fill('http://127.0.0.1:8875');
    prompt = await copied(); assert(prompt.includes('http://127.0.0.1:8875') && prompt.includes('/opt/agent/gl-agent-lab-mcp'));
    await page.locator('#quick-client').selectOption('http');
    prompt = await copied(); assert(prompt.includes('/v1/runs/quick-created-1/observe') && prompt.includes('/v1/runs/quick-created-1/actions') && prompt.includes('/v1/runs/quick-created-1/finish') && !prompt.includes('mcpServers'));
    await page.evaluate(() => { window.fixtureClipboard.deny = true; });
    await page.locator('#quick-copy-prompt').click();
    await page.locator('#quick-copy-status').filter({hasText:'Automatic copying'}).waitFor();
    assert.equal(await page.locator('#quick-setup-prompt').evaluate(el => el.value.slice(el.selectionStart,el.selectionEnd)), prompt);
    await Promise.all([page.waitForResponse(response => response.url().endsWith('/v1/runs/quick-created-1/report')), page.locator('#quick-refresh').click()]);
    assert.equal(await page.locator('#quick-setup-prompt').evaluate(el => el.value.slice(el.selectionStart,el.selectionEnd)), prompt);
    checks.tunnel_validation_http_copy_fallback_and_refresh_preserves_selection = 'pass';

    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.setViewportSize({width:1280,height:900});
    await chooseHistory('fixture-history');
    await page.waitForFunction(() => document.querySelector('#quick-engine-note').textContent.includes('Historical fixture replay'));
    assert.equal(await page.locator('#quick-setup-prompt').inputValue(), '');
    assert.equal(await page.locator('#quick-connection').isVisible(), false);
    await chooseHistory('quick-created-1'); await page.locator('#quick-connection').waitFor({state:'visible'});
    await page.evaluate(() => { window.fixtureClipboard.deny = false; });
    assert((await copied()).includes('fixture-run-key-1'));
    reports.get('quick-created-1').status = 'completed'; reports.get('quick-created-1').verdict = 'pass';
    await page.locator('#quick-refresh').click();
    await page.locator('#quick-connection').waitFor({state:'hidden'});
    assert.equal(await page.locator('#quick-setup-prompt').inputValue(), '');
    assert.equal(await page.locator('#quick-copy-prompt').isDisabled(), true);
    checks.history_switch_preserves_only_active_in_memory_keys_and_ended_runs_clear = 'pass';

    for (const format of ['html','json','junit']) {
      const promise = page.waitForEvent('download'); await page.locator('#quick-export-' + format).click();
      const download = await promise, chunks = [];
      for await (const chunk of await download.createReadStream()) chunks.push(chunk);
      const exported = Buffer.concat(chunks).toString();
      assert(!exported.includes(owner) && !exported.includes('fixture-run-key-'));
      assert(download.suggestedFilename().endsWith(format === 'junit' ? '.xml' : '.' + format));
    }
    checks.existing_backend_json_html_and_junit_exports = 'pass';

    await reviewCreate('safe');
    await page.waitForFunction(() => document.querySelector('#quick-meta').textContent.includes('Scripted reference'));
    assert.equal(await page.locator('#quick-connection').isVisible(), false);
    assert.match(await page.locator('#quick-observation-status').innerText(), /does not evaluate your own agent/);
    await page.locator('#quick-cancel').click();
    await page.waitForFunction(() => document.querySelector('#quick-meta').textContent.includes('cancelled'));
    checks.scripted_reference_label_and_existing_run_cancel = 'pass';

    holdPreview = true;
    await page.locator('#quick-scenario').selectOption('escrow-normal');
    let heldTimer;
    const heldReady = new Promise((resolve, reject) => {
      heldTimer = setTimeout(() => reject(new Error('Quick preview request did not arrive')), 10000);
      previewHeldReady = () => { clearTimeout(heldTimer); resolve(); };
    });
    await page.locator('#quick-preview').click();
    await heldReady;
    await page.locator('#quick-scenario').selectOption('escrow-provisional');
    await pendingPreview.route.fulfill({json:pendingPreview.value}); pendingPreview.resolve(); pendingPreview = null; holdPreview = false;
    await page.waitForFunction(() => !document.querySelector('#quick-preview').disabled);
    assert.equal(await page.locator('#quick-review').isVisible(), false);
    checks.stale_preview_cannot_approve_changed_selection = 'pass';

    await page.setViewportSize({width:390,height:844});
    await chooseHistory('quick-created-1');
    await page.locator('#quick-detail').waitFor({state:'visible'});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.equal(await page.locator('#test-mode').isVisible(), true);
    checks.quick_mode_and_report_fit_mobile_viewport = 'pass';
    await page.setViewportSize({width:1280,height:900});

    await page.locator('#disconnect').click(); await page.locator('#auth').waitFor({state:'visible'});
    assert.equal(await page.locator('#quick-setup-prompt').inputValue(), '');
    await page.goto(origin + '/assets/index.html#token=' + owner);
    await page.waitForURL(url => url.pathname === '/assets/workflows.html');
    await page.locator('#workspace').waitFor({state:'visible'});
    await page.waitForFunction(() => !document.querySelector('#quick-preview').disabled);
    assert.equal(await page.locator('#test-mode').inputValue(), 'glsim');
    assert(!page.url().includes(owner));
    await chooseHistory('quick-created-2'); await page.locator('#quick-detail').waitFor({state:'visible'});
    assert.equal(await page.locator('#quick-connection').isVisible(), false);
    checks.lock_clears_keys_and_legacy_url_redirects_to_single_dashboard = 'pass';
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    const writes = requests.filter(value => value.startsWith('POST '));
    assert(writes.every(value => /^POST \/v1\/quick-tests\/(preview|runs)$/.test(value) || value === 'POST /v1/runs/quick-created-2/cancel'));
    checks.live_requests = 0;
    console.log(JSON.stringify(checks));
  } finally {
    if (pendingPreview) { await pendingPreview.route.abort(); pendingPreview.resolve(); }
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
