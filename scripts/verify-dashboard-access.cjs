// Real browser + temporary single-process Lab + owner CLI. No Studio or model calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const {spawn, spawnSync} = require('node:child_process');
const {chromium} = require('playwright');

let stage = 'initialize temporary Lab';

async function stopService(service) {
  if (service.exitCode !== null || service.signalCode !== null) return;
  const closed = new Promise(resolve => service.once('close', resolve));
  if (process.platform === 'win32') {
    // A Windows venv Python executable launches a separate interpreter process.
    // Stop only this harness-owned tree, not every Python process on the host.
    const stopped = spawnSync(path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'taskkill.exe'),
      ['/PID', String(service.pid), '/T', '/F'], {windowsHide:true, stdio:'ignore', timeout:10000});
    if (stopped.status !== 0 && service.exitCode === null && service.signalCode === null) {
      throw new Error('Could not stop the temporary Lab process tree');
    }
  } else {
    service.kill('SIGTERM');
  }
  let timer;
  try {
    await Promise.race([closed, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Temporary Lab did not close after shutdown')), 10000);
    })]);
  } finally {
    clearTimeout(timer);
  }
}

(async () => {
  const data = fs.mkdtempSync(path.join(os.tmpdir(), 'lab-browser-access-'));
  const python = path.resolve(process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
  const probe = net.createServer();
  await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
  const port = probe.address().port;
  await new Promise(resolve => probe.close(resolve));
  const origin = `http://127.0.0.1:${port}`;
  const env = {...process.env}; delete env.LAB_URL; delete env.LAB_TOKEN;
  const service = spawn(python, ['-m', 'genlayer_agent_lab.cli', 'serve', '--data-dir', data, '--port', String(port)], {env, stdio:'ignore', windowsHide:true});
  let browser;
  try {
    stage = 'start temporary Lab';
    const deadline = Date.now() + 30000;
    for (;;) {
      try { if ((await fetch(origin + '/health')).ok) break; } catch {}
      if (Date.now() >= deadline || service.exitCode !== null) throw new Error('Temporary Lab failed to start');
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    stage = 'open locked dashboard';
    browser = await chromium.launch({headless:true});
    const context = await browser.newContext({permissions:['clipboard-read','clipboard-write']});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    // Rendering environment health must not invoke Docker in this auth check.
    await page.route('**/v1/onboarding/environment*', route => route.fulfill({json:{ready:true, checks:[]}}));
    await page.goto(origin);
    assert.equal(await page.locator('#manual-auth').getAttribute('open'), null);
    await page.getByRole('button', {name:'Connect this browser', exact:true}).click();
    const prompt = page.locator('#pair-prompt');
    await page.locator('#pair-panel').waitFor({state:'visible'});
    stage = 'copy private browser sign-in request';
    const message = await prompt.inputValue();
    const code = message.match(/code is ([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})/)[1];
    await page.getByRole('button', {name:'Copy sign-in request', exact:true}).click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), message);
    const admin = fs.readFileSync(path.join(data, 'admin.token'), 'utf8').trim();
    assert(!message.includes(admin));
    assert.equal(await page.locator('#workspace').isVisible(), false);
    stage = 'approve through owner CLI';
    const approved = spawnSync(python, ['-m','genlayer_agent_lab.cli','dashboard','approve',code,
      '--data-dir',data,'--port',String(port)], {env, encoding:'utf8', timeout:20000, windowsHide:true});
    assert.equal(approved.status, 0, 'Owner CLI approval failed');
    assert(!String(approved.stdout).includes(admin));
    stage = 'claim independent browser session';
    await page.locator('#workspace').waitFor({state:'visible'});
    const stored = await page.evaluate(() => sessionStorage.getItem('genlayer-agent-lab-admin-token'));
    assert(stored && stored !== admin);
    assert.equal(new URL(page.url()).hash, '');
    stage = 'verify unpaired browser and manual copy fallback';
    const fresh = await context.newPage();
    await fresh.goto(origin);
    assert.equal(await fresh.locator('#workspace').isVisible(), false, 'Unpaired browser must remain locked');
    await fresh.getByRole('button', {name:'Connect this browser', exact:true}).click();
    await fresh.locator('#pair-panel').waitFor({state:'visible'});
    await fresh.evaluate(() => Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{
      writeText:async () => { throw new Error('denied'); }
    }}));
    await fresh.getByRole('button', {name:'Copy sign-in request', exact:true}).click();
    assert.match(await fresh.locator('#pair-status').innerText(), /Copy the selected/);
    assert.equal(await fresh.locator('#pair-details').getAttribute('open'), '');
    stage = 'verify responsive sign-in layout';
    await fresh.setViewportSize({width:390,height:844});
    assert.equal(await fresh.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    const output = path.resolve('.lab/dashboard-access'); fs.mkdirSync(output, {recursive:true});
    await fresh.screenshot({path:path.join(output,'sign-in-mobile.png'),fullPage:true});
    await fresh.setViewportSize({width:1280,height:900});
    await fresh.screenshot({path:path.join(output,'sign-in-desktop.png'),fullPage:true});
    assert.deepEqual(errors, []);
    console.log('Dashboard sign-in passed: private CLI approval, browser-only claim, independent session, manual copy fallback, mobile layout.');
  } catch (error) {
    error.verificationStage = stage;
    throw error;
  } finally {
    try {
      stage = 'close verification browser';
      if (browser) await browser.close();
    } finally {
      stage = 'stop temporary Lab process tree';
      await stopService(service);
    }
    stage = 'remove verified temporary data directory';
    const resolved = path.resolve(data);
    if (path.dirname(resolved) !== path.resolve(os.tmpdir()) || !path.basename(resolved).startsWith('lab-browser-access-')) {
      throw new Error('Unexpected temporary directory');
    }
    try {
      fs.rmSync(resolved, {recursive:true, force:true, maxRetries:10, retryDelay:200});
    } catch (error) {
      if (!['EPERM', 'EACCES', 'EBUSY'].includes(error.code)) throw error;
      console.warn('Temporary Lab stopped, but filesystem cleanup was blocked:', error.code,
        '\nPrivate test data remains at:', resolved);
    }
  }
})().catch(error => { console.error('Dashboard sign-in verification failed at', error.verificationStage || stage,
  ':', error.code || error.name,
  String(error.stack).split('\n').filter(line => /^\s+at /.test(line)).slice(0,3).join('\n'));
  process.exitCode = 1; });
