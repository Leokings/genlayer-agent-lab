// Development-only browser check. Credentials are read locally, never printed.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

(async () => {
  const dataDir = path.resolve(process.env.LAB_DATA_DIR || '.lab/demo');
  const token = fs.readFileSync(path.join(dataDir, 'admin.token'), 'utf8').trim();
  const baseURL = process.env.LAB_URL || 'http://127.0.0.1:8765';
  const browser = await chromium.launch({headless:true, ...(process.env.LAB_BROWSER_CHANNEL ? {channel:process.env.LAB_BROWSER_CHANNEL} : {})});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1100}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(baseURL);
    await page.locator('#token').fill(token);
    await page.locator('#auth-form button').click();
    await page.locator('#workspace').waitFor({state:'visible'});
    await page.locator('#runs tr').first().waitFor();
    assert.equal(await page.locator('#scenario option').count(), 18);
    let customStatus = 'not configured';
    if (process.env.LAB_VERIFY_BINDING) {
      await page.locator('#binding').selectOption(process.env.LAB_VERIFY_BINDING);
      assert.match(await page.locator('#runtime-label').innerText(), /Container GLSim/);
      await page.locator('#scenario').selectOption('escrow-normal');
      await page.locator('#agent').selectOption('safe');
      const requestPromise = page.waitForRequest(request => request.url().endsWith('/v1/runs') && request.method() === 'POST');
      await page.locator('#launch-button').click();
      const request = await requestPromise;
      assert.equal(request.postDataJSON().binding_id, process.env.LAB_VERIFY_BINDING);
      assert.equal(request.postDataJSON().backend, 'container-glsim');
      await page.waitForFunction(() => /completed|inconclusive/.test(document.querySelector('#detail-meta').textContent), null, {timeout:90000});
      const manifest = JSON.parse(await page.locator('#manifest').textContent());
      assert.equal(manifest.binding.id, process.env.LAB_VERIFY_BINDING);
      assert.match(manifest.binding.source_sha256, /^[a-f0-9]{64}$/);
      assert.equal('source' in manifest.binding, false);
      customStatus = (await page.locator('#detail-meta').innerText()).includes('completed') ? 'completed' : 'inconclusive';
      await page.screenshot({path:path.join(dataDir,'dashboard-custom.png'),fullPage:true});
      await page.locator('#binding').selectOption('');
    }
    await page.locator('#scenario').selectOption('escrow-provisional');
    await page.locator('#agent').selectOption('unsafe');
    const newRunResponse = page.waitForResponse(response => response.url().endsWith('/v1/runs') && response.request().method() === 'POST');
    await page.locator('#launch-button').click();
    const newRun = await (await newRunResponse).json();
    await page.waitForFunction(id => {
      const value = document.querySelector('#detail-meta').textContent;
      return value.includes(id) && value.includes('completed');
    }, newRun.run_id, {timeout:60000});
    assert.match(await page.locator('#findings').innerText(), /not final|already applied/);
    assert.equal(await page.locator('#grades .grade').count(), 4);
    await page.locator('#detail').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(dataDir,'dashboard-report.png'),fullPage:true});
    const comparison = await page.locator('#compare option').nth(1).getAttribute('value');
    await page.locator('#compare').selectOption(comparison);
    await page.locator('#comparison table').waitFor();
    const downloadPromise = page.waitForEvent('download');
    await page.locator('[data-format="html"]').click();
    const download = await downloadPromise;
    await download.saveAs(path.join(dataDir,'browser-export.html'));
    assert.match(fs.readFileSync(path.join(dataDir,'browser-export.html'),'utf8'), /GenLayer Agent Lab/);
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(() => window.scrollTo(0,0));
    await page.screenshot({path:path.join(dataDir,'dashboard-mobile.png'),fullPage:true});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({dashboard:'pass',scenarios:18,customBinding:customStatus,launch:'pass',grades:'pass',compare:'pass',download:'pass',mobile:'pass',pageErrors:errors}));
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode=1; });
