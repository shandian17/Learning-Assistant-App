// 学习页接口契约测试：使用浏览器拦截 API，不调用真实模型。
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
    const page = await browser.newPage({ viewport: { width: 1100, height: 850 } });
    await page.addInitScript(() => localStorage.setItem('learning-assistant-language', 'zh-CN'));
    const errors = [];
    const creates = [];
    const messages = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/v1/**', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const respond = (data, status = 200) => route.fulfill({ status, json: { data } });
      if (url.pathname === '/api/v1/materials') {
        return respond({ items: [{ id: 'm1', filename: '课程.md', status: 'ready' }, { id: 'm2', filename: '处理中.pdf', status: 'processing' }], total: 2, page: 1, page_size: 100 });
      }
      if (url.pathname === '/api/v1/chat/sessions' && request.method() === 'POST') {
        creates.push(request.postDataJSON());
        return respond({ session_id: 's1', material_ids: ['m1'], created_at: '2026-09-16T10:00:00Z' }, 201);
      }
      if (url.pathname === '/api/v1/chat/sessions/s1/messages' && request.method() === 'POST') {
        messages.push(request.postDataJSON());
        return respond({
          user_message_id: 'u1',
          assistant_message: {
            id: 'a1', role: 'assistant', content: '依据资料回答。<img src=x onerror=alert(1)>', evidence_status: 'sufficient',
            citations: [{ material_id: 'm1', version_id: 'v1', chunk_id: 'c1', filename: '课程.md', locator: { heading: '注意力机制' } }],
            created_at: '2026-09-16T10:00:01Z'
          }
        }, 201);
      }
      return route.fulfill({ status: 404, json: { error: { message: 'not mocked' } } });
    });

    await page.goto(`http://127.0.0.1:${server.address().port}/#learn`);
    const source = page.getByLabel('用于对话：课程.md');
    await source.waitFor();
    assert.equal(await page.getByLabel('用于对话：处理中.pdf').count(), 0);
    assert.equal(await page.locator('#chat-input').isDisabled(), true);
    await source.check();
    await page.locator('#chat-input').fill('什么是注意力机制？');
    await page.locator('#chat-send').click();
    await page.locator('.chat-message.assistant').waitFor();
    assert.deepEqual(creates, [{ material_ids: ['m1'], language: 'zh-CN' }]);
    assert.deepEqual(messages, [{ content: '什么是注意力机制？', language: 'zh-CN' }]);
    assert.equal(await page.locator('.chat-message').count(), 2);
    assert.match(await page.locator('.chat-message.assistant').innerText(), /依据资料回答/);
    assert.equal(await page.locator('.chat-message img').count(), 0);
    assert.match(await page.locator('.chat-citations').innerText(), /课程\.md · 注意力机制/);
    assert.equal(await source.isDisabled(), true);
    await page.locator('#learn-new-session').click();
    assert.equal(await source.isDisabled(), false);
    assert.equal(await page.locator('.chat-message').count(), 0);
    assert.deepEqual(errors, []);
    console.log('PASS: ready-source filtering, chat session creation, message request/response contract, citations, XSS-safe output, and new-session reset.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
