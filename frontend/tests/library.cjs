// 浏览器交互测试：仅在测试中拦截 API，不启动或修改真实后台。
// 使用已安装的 Playwright 运行：node frontend/tests/library.cjs
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
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) { res.writeHead(404).end(); return; }
    res.setHeader('Content-Type', ({ '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript' })[path.extname(file)] || 'application/octet-stream');
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    const chrome = process.env.BROWSER_PATH;
    browser = await chromium.launch(chrome ? { executablePath: chrome, headless: true } : { headless: true });
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await page.addInitScript(() => localStorage.setItem('learning-assistant-language', 'zh-CN'));
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let items = [
      { id: '1', filename: '课程.pdf', file_size: 2048, status: 'ready', current_version_id: 'v1' },
      { id: '2', filename: '失败.docx', file_size: 1024, status: 'failed', error_message: '解析失败，请重试' },
      { id: '3', filename: '<img src=x onerror=alert(1)>.txt', file_size: 10, status: 'processing' }
    ];
    let duplicate = false;
    let deleteFailure = false;
    let uploadFailure = false;
    let listFailure = false;
    let conflictOnce = false;
    const uploads = [];
    const requests = [];
    await page.route('**/api/v1/**', async route => {
      const req = route.request();
      const url = new URL(req.url());
      requests.push([req.method(), url.pathname]);
      const respond = (data, status = 200) => route.fulfill({ status, json: { data } });
      if (url.pathname.endsWith('/check-name')) return respond({ duplicate, existing_material: { id: '1', current_version_id: 'v1' } });
      if (req.method() === 'GET') {
        if (listFailure) return route.fulfill({ status: 503, json: { error: { message: '服务暂不可用' } } });
        const pageIndex = Number(url.searchParams.get('page') || 1);
        // 故意每页只返回两项，确认前端会读取后续页。
        return respond({ items: items.slice((pageIndex - 1) * 2, pageIndex * 2), total: items.length });
      }
      if (req.method() === 'DELETE') {
        if (deleteFailure) return route.fulfill({ status: 500, json: { error: { message: '删除服务异常' } } });
        items = items.filter(item => item.id !== url.pathname.split('/').pop());
        return route.fulfill({ status: 204 });
      }
      if (url.pathname.endsWith('/retry')) {
        items.find(item => item.id === '2').status = 'processing';
        return respond({ material_id: '2', status: 'processing' }, 202);
      }
      if (req.method() === 'POST') {
        uploads.push(req.postDataBuffer().toString());
        if (conflictOnce) { conflictOnce = false; duplicate = true; return route.fulfill({ status: 409, json: { error: { code: 'VERSION_CONFLICT' } } }); }
        if (uploadFailure) return route.fulfill({ status: 500, json: { error: { message: '保存失败' } } });
        return respond({ material_id: 'new', status: 'processing' }, 202);
      }
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/#library`);
    await page.waitForFunction(() => document.querySelectorAll('#material-rows tr').length === 3);
    assert.equal(await page.locator('#material-rows img').count(), 0);
    assert.match(await page.locator('#material-rows').innerText(), /2.0 KB/);
    await page.getByRole('button', { name: '重试 失败.docx', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('#material-rows').textContent.includes('解析中') && !document.querySelector('#material-rows').textContent.includes('重试'));
    const file = name => ({ name, mimeType: 'application/octet-stream', buffer: Buffer.from('course material') });
    async function waitUploadDone() { await page.waitForFunction(() => !document.getElementById('upload-zone').disabled); }
    const chooserPromise = page.waitForEvent('filechooser');
    await page.locator('#upload-zone').click();
    await (await chooserPromise).setFiles(file('新资料.PDF'));
    await waitUploadDone();
    assert.match(await page.locator('#notifications').innerText(), /上传成功/);
    assert.ok(uploads.at(-1).includes('name="file"'));
    assert.ok(uploads.at(-1).includes('name="language"'));
    assert.ok(uploads.at(-1).includes('zh-CN'));
    for (const name of ['幻灯片.ppt', '幻灯片.pptx', '文档.doc', '文档.docx', '笔记.md', '笔记.markdown', '笔记.txt']) {
      await page.locator('#material-files').setInputFiles(file(name)); await waitUploadDone();
    }
    const beforeInvalid = uploads.length;
    await page.locator('#material-files').setInputFiles(file('程序.exe')); await waitUploadDone();
    assert.equal(uploads.length, beforeInvalid);
    assert.match(await page.locator('#notifications').innerText(), /格式不支持/);
    duplicate = true;
    await page.locator('#material-files').setInputFiles(file('课程.pdf'));
    await page.locator('#duplicate-dialog[open]').waitFor();
    await page.getByRole('button', { name: '取消', exact: true }).click(); await waitUploadDone();
    assert.equal(uploads.length, beforeInvalid);
    for (const [label, value] of [['保留两份', 'keep_both'], ['覆盖', 'replace']]) {
      await page.locator('#material-files').setInputFiles(file('课程.pdf'));
      await page.locator('#duplicate-dialog[open]').waitFor();
      await page.getByRole('button', { name: label, exact: true }).click(); await waitUploadDone();
      assert.ok(uploads.at(-1).includes(value));
      if (value === 'replace') assert.ok(uploads.at(-1).includes('expected_version_id'));
    }
    duplicate = false; conflictOnce = true;
    await page.locator('#material-files').setInputFiles(file('冲突.pdf'));
    await page.locator('#duplicate-dialog[open]').waitFor();
    await page.keyboard.press('Escape'); await waitUploadDone();
    duplicate = false;
    const transfer = await page.evaluateHandle(() => { const dt = new DataTransfer(); dt.items.add(new File(['notes'], '拖拽.txt', { type: 'text/plain' })); return dt; });
    await page.locator('#upload-zone').dispatchEvent('dragenter', { dataTransfer: transfer });
    assert.equal(await page.locator('#upload-zone').evaluate(el => el.classList.contains('is-dragging')), true);
    await page.locator('#upload-zone').dispatchEvent('drop', { dataTransfer: transfer }); await waitUploadDone();
    uploadFailure = true;
    await page.locator('#material-files').setInputFiles(file('错误.txt')); await waitUploadDone();
    assert.match(await page.locator('#notifications').innerText(), /保存失败/);
    deleteFailure = true;
    await page.getByRole('button', { name: '删除 课程.pdf', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('#notifications').textContent.includes('删除失败'));
    assert.equal(await page.locator('#material-rows tr').count(), 3);
    deleteFailure = false;
    await page.getByRole('button', { name: '删除 课程.pdf', exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll('#material-rows tr').length === 2);
    listFailure = true;
    await page.locator('#refresh-materials').click();
    await page.locator('#library-error:not([hidden])').waitFor();
    assert.equal(await page.locator('#material-rows tr').count(), 2);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(errors, []);
    console.log('PASS: pagination, file types, click and drop upload, duplicate cancel/keep/replace, conflict recheck, retry, delete success/failure, upload failure, stale-list error, XSS-safe names, mobile overflow.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
