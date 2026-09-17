const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');

(async () => {
  const root = path.resolve(__dirname, '..');
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    const file = path.resolve(root, '.' + (pathname === '/' ? '/index.html' : pathname));
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) return res.writeHead(404).end();
    res.setHeader('Content-Type', ({ '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript' })[path.extname(file)] || 'application/octet-stream');
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_PATH ? { executablePath: process.env.BROWSER_PATH } : {}) });
    const context = await browser.newContext({ locale: 'en-CA' });
    const page = await context.newPage();
    const uploads = [];
    const assessments = [];
    await page.route('**/api/v1/**', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const respond = (data, status = 200) => route.fulfill({ status, json: { data } });
      if (url.pathname.endsWith('/materials/check-name')) return respond({ duplicate: false });
      if (url.pathname.endsWith('/materials') && request.method() === 'POST') {
        uploads.push(request.postDataBuffer().toString());
        return respond({ material_id: 'm2', version_id: 'v2', filename: 'mixed.txt', language: 'en', status: 'processing' }, 202);
      }
      if (url.pathname.endsWith('/materials')) return respond({ items: [{ id: 'm1', filename: 'course.txt', language: 'en', status: 'ready' }], total: 1, page: 1, page_size: 100 });
      if (url.pathname.endsWith('/assessments') && request.method() === 'POST') {
        assessments.push(request.postDataJSON());
        return respond({ assessment_id: 'a1', status: 'generating' }, 202);
      }
      if (url.pathname.endsWith('/assessments')) return respond({ items: [], total: 0, page: 1, page_size: 10 });
      if (url.pathname.endsWith('/assessments/a1')) return respond({ status: 'generation_failed', error_message: 'test stop' });
      return route.fulfill({ status: 404, json: { error: { message: 'not mocked' } } });
    });

    await page.goto(`http://127.0.0.1:${server.address().port}/#library`);
    assert.equal(await page.locator('html').getAttribute('lang'), 'en');
    assert.equal(await page.locator('[data-route="learn"]').innerText(), 'Learn');
    assert.equal(await page.locator('#library-title').innerText(), 'Your materials are where learning begins.');
    assert.equal(await page.locator('#language-toggle').getAttribute('data-language'), 'en');
    for (const routeName of ['learn', 'library', 'assessment', 'progress', 'reports']) {
      await page.locator(`[data-route="${routeName}"]`).click();
      const text = (await page.locator('body').innerText()).replace('中文', '');
      assert.equal(/[\u3400-\u9fff]/.test(text), false, `untranslated text on ${routeName}: ${text.match(/[\u3400-\u9fff][^\n]*/)?.[0] || ''}`);
    }
    await page.locator('[data-route="library"]').click();

    await page.locator('#material-files').setInputFiles({ name: 'mixed.txt', mimeType: 'text/plain', buffer: Buffer.from('中文 and English stay unchanged') });
    await page.waitForFunction(() => document.querySelector('#notifications').textContent.includes('uploaded'));
    assert.ok(uploads[0].includes('name="language"'));
    assert.ok(uploads[0].includes('en'));

    await page.locator('[data-route="assessment"]').click();
    await page.getByLabel('course.txt', { exact: true }).check();
    await page.locator('#assessment-start').click();
    await page.waitForFunction(() => document.querySelector('#assessment-error:not([hidden])'));
    assert.equal(assessments[0].language, 'en');

    await page.locator('#language-toggle').click();
    await page.waitForLoadState('domcontentloaded');
    assert.equal(await page.locator('html').getAttribute('lang'), 'zh-CN');
    assert.equal(await page.locator('[data-route="learn"]').innerText(), '学习');
    assert.equal(await page.evaluate(() => localStorage.getItem('learning-assistant-language')), 'zh-CN');
    console.log('PASS: browser-language default, English UI, upload and assessment language propagation, and persisted Chinese toggle.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
