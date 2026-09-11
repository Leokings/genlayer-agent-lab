// Standalone setup page: real clipboard, denied clipboard fallback and mobile layout.
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
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => new URL(route.request().url()).origin === origin
      ? route.continue() : route.abort());
    await page.goto(origin);
    const prompt = await page.locator('#setup-prompt').inputValue();
    assert.match(prompt, /installing missing prerequisites/);
    assert.match(prompt, /Docker with Compose/);
    await page.getByRole('button', {name:'Copy setup prompt', exact:true}).click();
    await page.getByRole('status').filter({hasText:'Copied.'}).waitFor();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), prompt);
    await page.screenshot({path:path.join(output, 'desktop.png'), fullPage:true});

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

    await page.setViewportSize({width:390,height:844});
    await page.locator('#prompt-details').evaluate(el => { el.open = false; });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({path:path.join(output, 'mobile.png'), fullPage:true});
    assert.deepEqual(errors, []);
    console.log('3 setup-page checks passed: clipboard, manual fallback, mobile layout.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
