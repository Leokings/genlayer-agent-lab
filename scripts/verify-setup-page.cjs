// Standalone setup page: clipboard, progressive opening guide, ports and mobile layout.
// No Lab, model, server credentials or external requests are used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {chromium} = require('playwright');

(async () => {
  const html = fs.readFileSync(path.resolve('docs/START.html'));
  const output = path.resolve('.lab/setup-page');
  fs.mkdirSync(output, {recursive:true});
  const server = http.createServer((request, response) => {
    response.writeHead(200, {'Content-Type':'text/html; charset=utf-8'});
    response.end(html);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  let browser;
  try {
    browser = await chromium.launch({headless:true});
    const context = await browser.newContext({viewport:{width:1280,height:900},
      permissions:['clipboard-read','clipboard-write']});
    const page = await context.newPage();
    const errors = [];
    const externalRequests = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      if (new URL(route.request().url()).origin === origin) return route.continue();
      externalRequests.push(route.request().url());
      return route.abort();
    });
    await page.goto(origin);
    const prompt = await page.locator('#setup-prompt').inputValue();
    assert.match(prompt, /installing missing prerequisites/);
    assert.match(prompt, /Docker with Compose/);
    await page.getByRole('button', {name:'Copy setup prompt', exact:true}).click();
    await page.getByRole('status').filter({hasText:'Copied.'}).waitFor();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), prompt);
    await page.getByRole('link', {name:'Prepare a test from my contract', exact:true}).click();
    assert.equal(new URL(page.url()).hash, '#prepare-contract-test');
    const authoringPrompt = await page.locator('#authoring-prompt').inputValue();
    assert.match(authoringPrompt, /skills\/prepare-genlayer-lab-test\/SKILL.md/);
    assert.match(authoringPrompt, /validation result/);
    assert.match(authoringPrompt, /Do not create or start a test/);
    assert.match(authoringPrompt, /without access to this conversation, its memories or private test files/);
    await page.getByRole('button', {name:'Copy test-preparation prompt', exact:true}).click();
    await page.locator('#authoring-copy-status').filter({hasText:'Copied.'}).waitFor();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), authoringPrompt);
    await page.screenshot({path:path.join(output, 'desktop.png'), fullPage:true});

    assert.equal(await page.locator('#opening-help').isVisible(), false);
    await page.getByRole('button', {name:'This computer', exact:true}).click();
    assert.equal(await page.locator('#open-local').isVisible(), true);
    assert.equal(await page.locator('#open-vps').isVisible(), false);
    assert.equal(await page.locator('#local-link').getAttribute('href'), 'http://127.0.0.1:8765/');
    assert.equal(await page.locator('#sign-in-help').isVisible(), true);

    await page.getByRole('button', {name:'A VPS', exact:true}).click();
    assert.equal(await page.locator('#open-local').isVisible(), false);
    assert.equal(await page.locator('#sign-in-help').isVisible(), false);
    assert.equal(await page.locator('[data-step]:visible').count(), 1);
    assert.equal(await page.locator('[data-step="0"]').isVisible(), true);
    assert.doesNotMatch(await page.locator('#open-vps').innerText(), /8875|8765/);
    assert.equal(await page.getByRole('link', {name:'Already connected? Open dashboard', exact:true}).isVisible(), true);
    assert.equal(await page.locator('#vps-existing-link').getAttribute('href'), 'http://127.0.0.1:8875/');
    for (let step = 1; step <= 4; step++) {
      await page.getByRole('button', {name:'Done, next step', exact:true}).click();
      assert.equal(await page.locator('[data-step]:visible').count(), 1);
      assert.equal(await page.locator(`[data-step="${step}"]`).isVisible(), true);
    }
    assert.equal(await page.locator('#vps-link').getAttribute('href'), 'http://127.0.0.1:8875/');
    assert.equal(await page.locator('#step-next').isVisible(), false);
    assert.equal(await page.locator('#sign-in-help').isVisible(), true);
    await page.getByRole('button', {name:'Back', exact:true}).click();
    assert.equal(await page.locator('[data-step="3"]').isVisible(), true);

    await page.locator('#port-settings summary').click();
    for (const invalid of ['0', '65536', '-1', '1e3', '88.75']) {
      await page.locator('#server-port').fill(invalid);
      assert.equal(await page.locator('#server-port').getAttribute('aria-invalid'), 'true');
      assert.equal(await page.locator('#vps-link').getAttribute('href'), null);
      assert.equal(await page.locator('#vps-existing-link').getAttribute('href'), null);
      assert.equal(await page.locator('#vps-existing-link').getAttribute('aria-disabled'), 'true');
      assert.equal(await page.locator('#step-next').isDisabled(), true);
      assert.equal(await page.locator('#copy-continuation').isDisabled(), true);
      assert.equal(await page.locator('#continuation-prompt').inputValue(), '');
    }
    await page.locator('#server-port').fill('8795');
    await page.locator('#local-port').fill('65536');
    assert.equal(await page.locator('#vps-link').getAttribute('href'), null);
    await page.locator('#local-port').fill('8876');
    assert.equal(await page.locator('#step-next').isDisabled(), false);
    assert.equal(await page.locator('#vps-link').getAttribute('href'), 'http://127.0.0.1:8876/');
    assert.equal(await page.locator('#vps-existing-link').getAttribute('href'), 'http://127.0.0.1:8876/');
    assert.equal(await page.locator('[data-server-port]').textContent(), '8795');
    assert.equal(await page.locator('[data-local-port]').textContent(), '8876');

    await page.locator('#continuation > summary').click();
    await page.getByRole('button', {name:'Copy continuation prompt', exact:true}).click();
    await page.locator('#continuation-status').filter({hasText:'Copied.'}).waitFor();
    const continuation = await page.locator('#continuation-prompt').inputValue();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), continuation);
    assert.match(continuation, /do not reinstall or reset/);
    assert.match(continuation, /Lab port 8795 and local forwarding port 8876; I use Termius/);
    assert.match(continuation, /gl-agent-lab dashboard approve CODE/);
    assert.match(continuation, /inspect the installed CLI help for dashboard approve support/);
    assert.match(continuation, /update the existing checkout while preserving local changes and saved data/);
    assert.doesNotMatch(continuation, /--show-token|your-user@your-server|#token=/);

    await page.getByRole('button', {name:'Windows PowerShell', exact:true}).click();
    assert.equal(await page.locator('#termius-guide').isVisible(), false);
    assert.equal(await page.locator('#powershell-guide').isVisible(), true);
    assert.equal(await page.locator('#ssh-host').inputValue(), '');
    assert.equal(await page.locator('#ssh-user').inputValue(), '');
    assert.equal(await page.locator('#copy-ssh-command').isDisabled(), true);
    assert.equal(await page.locator('#ssh-command').inputValue(), '');
    await page.locator('#ssh-host').fill('203.0.113.5');
    assert.equal(await page.locator('#copy-ssh-command').isDisabled(), true);
    await page.locator('#ssh-user').fill('linuxuser');
    await page.locator('#ssh-port').fill('2222');
    const sshCommand = 'ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:8876:127.0.0.1:8795 -p 2222 linuxuser@203.0.113.5';
    assert.equal(await page.locator('#ssh-command').inputValue(), sshCommand);
    assert.equal(await page.locator('#powershell-link').getAttribute('href'), 'http://127.0.0.1:8876/');
    await page.getByRole('button', {name:'Copy PowerShell command', exact:true}).click();
    await page.locator('#ssh-copy-status').filter({hasText:'Copied.'}).waitFor();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), sshCommand);
    assert.match(await page.locator('#continuation-prompt').inputValue(), /I use Windows PowerShell.*linuxuser@203\.0\.113\.5 on port 2222/);
    assert.doesNotMatch(sshCommand, /StrictHostKeyChecking|YOUR|your-user/);
    for (const invalid of ['-oProxyCommand=calc', 'name;whoami', '$(whoami)', 'name&whoami', 'name|whoami', 'name`whoami', 'name"', "name'", 'name@host', 'name space']) {
      await page.locator('#ssh-user').fill(invalid);
      assert.equal(await page.locator('#copy-ssh-command').isDisabled(), true, invalid);
      assert.equal(await page.locator('#ssh-command').inputValue(), '', invalid);
      assert.equal(await page.locator('#powershell-ready').isVisible(), false);
    }
    assert.equal(await page.evaluate(() => validSshUser('name\n')), false);
    await page.locator('#ssh-user').fill('linuxuser');
    for (const invalid of ['-evil', 'host;whoami', 'host&whoami', '$(whoami)', 'host`whoami', 'host|whoami', 'https://host.test', 'user@host.test', 'host name', 'host"', "host'", '256.1.1.1', '1.2.3', '01.2.3.4', 'bad..host', '[::1]']) {
      await page.locator('#ssh-host').fill(invalid);
      assert.equal(await page.locator('#copy-ssh-command').isDisabled(), true, invalid);
      assert.equal(await page.locator('#ssh-command').inputValue(), '', invalid);
    }
    assert.equal(await page.evaluate(() => validSshHost('host\n')), false);
    assert.equal(await page.evaluate(() => validPort('22\n')), false);
    await page.locator('#ssh-host').fill('lab.example.test');
    await page.locator('#ssh-port').fill('65536');
    assert.equal(await page.locator('#copy-ssh-command').isDisabled(), true);
    await page.locator('#ssh-port').fill('22');
    assert.match(await page.locator('#ssh-command').inputValue(), /-p 22 linuxuser@lab\.example\.test$/);
    await page.getByRole('button', {name:'Termius', exact:true}).click();
    assert.equal(await page.locator('#powershell-guide').isVisible(), false);
    assert.equal(await page.locator('[data-step="3"]').isVisible(), true);
    assert.equal(await page.locator('#ssh-command').inputValue(), '');
    await page.getByRole('button', {name:'Windows PowerShell', exact:true}).click();
    assert.equal(await page.locator('#ssh-host').inputValue(), 'lab.example.test');
    assert.equal(await page.locator('#copy-ssh-command').isDisabled(), false);

    await page.goto(`${origin}/?location=vps&lab_port=8795#open-dashboard`);
    assert.equal(await page.locator('#choose-vps').getAttribute('aria-pressed'), 'true');
    assert.equal(await page.locator('#server-port').inputValue(), '8795');
    assert.equal(await page.locator('[data-step="0"]').isVisible(), true);
    assert.equal(await page.locator('[data-server-port]').textContent(), '8795');
    await page.goto(`${origin}/?location=vps&lab_port=8765%2F%2Fevil.test#open-dashboard`);
    assert.equal(await page.locator('#server-port').inputValue(), '');
    assert.equal(await page.locator('#port-settings').getAttribute('open'), '');
    assert.equal(await page.locator('#step-next').isDisabled(), true);
    assert.equal(await page.locator('#vps-link').getAttribute('href'), null);
    assert.equal(await page.locator('#vps-existing-link').getAttribute('href'), null);
    await page.goto(`${origin}/?location=unknown&lab_port=8795`);
    assert.equal(await page.locator('#opening-help').isVisible(), false);
    await page.goto(`${origin}/?location=local&lab_port=8795#open-dashboard`);
    assert.equal(await page.locator('#local-link').getAttribute('href'), 'http://127.0.0.1:8795/');

    await page.evaluate(() => {
      Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{
        writeText:async () => { throw new Error('Permission denied'); }
      }});
    });
    await page.getByRole('button', {name:'Copy setup prompt', exact:true}).click();
    await page.getByRole('status').filter({hasText:'Automatic copying is unavailable'}).waitFor();
    assert.equal(await page.locator('#prompt-details').getAttribute('open'), '');
    assert.equal(await page.locator('#setup-prompt').evaluate(el =>
      el.value.slice(el.selectionStart, el.selectionEnd)), prompt);
    await page.getByRole('button', {name:'Copy test-preparation prompt', exact:true}).click();
    await page.locator('#authoring-copy-status').filter({hasText:'Automatic copying is unavailable'}).waitFor();
    assert.equal(await page.locator('#authoring-prompt-details').getAttribute('open'), '');
    assert.equal(await page.locator('#authoring-prompt').evaluate(el =>
      el.value.slice(el.selectionStart, el.selectionEnd)), authoringPrompt);
    await page.locator('#continuation > summary').click();
    await page.getByRole('button', {name:'Copy continuation prompt', exact:true}).click();
    await page.locator('#continuation-status').filter({hasText:'Automatic copying is unavailable'}).waitFor();
    assert.equal(await page.locator('#continuation-details').getAttribute('open'), '');
    assert.equal(await page.locator('#continuation-prompt').evaluate(el =>
      el.value.slice(el.selectionStart, el.selectionEnd)), await page.locator('#continuation-prompt').inputValue());

    await page.setViewportSize({width:390,height:844});
    await page.locator('#prompt-details').evaluate(el => { el.open = false; });
    await page.getByRole('button', {name:'A VPS', exact:true}).click();
    await page.locator('#continuation').evaluate(el => { el.open = false; });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({path:path.join(output, 'mobile.png'), fullPage:true});
    await page.getByRole('button', {name:'Done, next step', exact:true}).click();
    await page.getByRole('button', {name:'Done, next step', exact:true}).click();
    await page.locator('#port-settings summary').click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({path:path.join(output, 'mobile-vps.png'), fullPage:true});
    await page.getByRole('button', {name:'Windows PowerShell', exact:true}).click();
    await page.locator('#ssh-host').fill('203.0.113.5');
    await page.locator('#ssh-user').fill('linuxuser');
    await page.getByRole('button', {name:'Copy PowerShell command', exact:true}).click();
    await page.locator('#ssh-copy-status').filter({hasText:'Automatic copying is unavailable'}).waitFor();
    assert.equal(await page.locator('#ssh-command-details').getAttribute('open'), '');
    assert.equal(await page.locator('#ssh-command').evaluate(el => el.value.slice(el.selectionStart, el.selectionEnd)), await page.locator('#ssh-command').inputValue());
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({path:path.join(output, 'mobile-powershell.png'), fullPage:true});
    assert.deepEqual(errors, []);
    assert.deepEqual(externalRequests, []);
    console.log('12 setup-page checks passed: setup clipboard, contract-authoring discovery/copy/fallback, local opening, VPS steps, port validation, continuation clipboard, connection method switching, PowerShell command clipboard/custom ports, SSH injection rejection, installer query handoff, manual fallbacks, mobile layout. External requests: 0.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
