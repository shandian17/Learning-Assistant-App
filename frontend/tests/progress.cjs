// 在本地浏览器中拦截模拟 API；不会读写真实学习资料或笔记。
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
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
    const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
    await page.addInitScript(() => localStorage.setItem('learning-assistant-language', 'zh-CN'));
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    const notes = new Map([['m1', '原有笔记'], ['m2', '这是一段很长的学习笔记。'.repeat(30)]]);
    const noteWrites = [];
    let failNextNote = false;
    let failSummary = false;
    const points = [
      { knowledge_point_id: 'k1', name: 'Transformer 架构', material_id: 'm1', filename: '大模型基础.md', mastery_status: 'mastered', latest_score: 1 },
      { knowledge_point_id: 'k2', name: '注意力机制', material_id: 'm1', filename: '大模型基础.md', mastery_status: 'partial', latest_score: .5 },
      { knowledge_point_id: 'k3', name: '提示词设计', material_id: 'm2', filename: '<img src=x onerror=alert(1)>.pdf', mastery_status: 'needs_review', latest_score: 0 },
      { knowledge_point_id: 'k4', name: '模型评估', material_id: 'm2', filename: '<img src=x onerror=alert(1)>.pdf', mastery_status: 'unassessed', latest_score: null }
    ];
    const materials = () => [
      { material_id: 'm1', filename: '大模型基础.md', knowledge_points_total: 2, assessed_points: 2, mastered_points: 1, partial_points: 1, needs_review_points: 0, coverage_rate: 100, mastery_rate: 75, note: { content: notes.get('m1'), updated_at: '2026-09-16T10:00:00Z' } },
      { material_id: 'm2', filename: '<img src=x onerror=alert(1)>.pdf', knowledge_points_total: 2, assessed_points: 1, mastered_points: 0, partial_points: 0, needs_review_points: 1, coverage_rate: 50, mastery_rate: 0, note: { content: notes.get('m2'), updated_at: null } },
      { material_id: 'm3', filename: '尚未测评.txt', knowledge_points_total: 0, assessed_points: 0, mastered_points: 0, partial_points: 0, needs_review_points: 0, coverage_rate: null, mastery_rate: null, note: { content: '', updated_at: null } }
    ];
    await page.route('**/api/v1/**', async route => {
      const req = route.request();
      const url = new URL(req.url());
      const respond = (data, status = 200) => route.fulfill({ status, json: { data } });
      if (url.pathname === '/api/v1/materials' && req.method() === 'GET') return respond({ items: [], total: 0 });
      if (url.pathname === '/api/v1/progress/summary') {
        if (failSummary) return route.fulfill({ status: 503, json: { error: { message: '统计服务暂不可用' } } });
        return respond({ total_materials: 3, mastered_materials: 0, assessments_count: 6, average_score_rate: 78.5, weak_points_count: 2 });
      }
      if (url.pathname === '/api/v1/progress/knowledge-points') {
        const number = Number(url.searchParams.get('page'));
        return respond({ items: points.slice((number - 1) * 2, number * 2), total: points.length });
      }
      if (url.pathname === '/api/v1/progress/materials') {
        const number = Number(url.searchParams.get('page'));
        return respond({ items: materials().slice((number - 1) * 2, number * 2), total: materials().length });
      }
      const noteMatch = url.pathname.match(/^\/api\/v1\/materials\/([^/]+)\/note$/);
      if (noteMatch && req.method() === 'PUT') {
        const id = decodeURIComponent(noteMatch[1]);
        const content = req.postDataJSON().content;
        noteWrites.push({ id, content });
        if (failNextNote) {
          failNextNote = false;
          return route.fulfill({ status: 503, json: { error: { message: '保存服务暂不可用' } } });
        }
        if (content === '第一次输入') await new Promise(resolve => setTimeout(resolve, 350));
        notes.set(id, content);
        return respond({ material_id: id, content, updated_at: '2026-09-16T12:34:00Z' });
      }
      return route.fulfill({ status: 404, json: { error: { message: 'Not found' } } });
    });

    await page.goto(`http://127.0.0.1:${server.address().port}/#progress`);
    await page.waitForFunction(() => document.getElementById('progress-total-materials').textContent === '3');
    assert.equal(await page.locator('#progress-mastered-materials').innerText(), '0');
    assert.equal(await page.locator('#progress-assessments').innerText(), '6');
    assert.equal(await page.locator('#progress-average-score').innerText(), '78.5%');
    assert.equal(await page.locator('#progress-weak-count').innerText(), '2');
    assert.equal(await page.locator('.knowledge-item').count(), 4);
    assert.equal(await page.locator('.weak-item').count(), 2);
    assert.equal(await page.locator('#radar-chart svg').count(), 1);
    assert.match(await page.locator('#radar-subtitle').innerText(), /3 \/ 4/);
    assert.equal(await page.locator('#knowledge-list img').count(), 0);
    assert.match(await page.locator('#knowledge-list').innerText(), /<img src=x onerror=alert\(1\)>\.pdf/);
    const unassessed = page.locator('.knowledge-item').filter({ hasText: '模型评估' });
    assert.equal(await unassessed.locator('[role="progressbar"]').getAttribute('aria-valuetext'), '未测评');
    assert.equal(await unassessed.locator('output').innerText(), '—');
    assert.match(await page.locator('#weak-list').innerText(), /注意力机制/);
    assert.match(await page.locator('#weak-list').innerText(), /提示词设计/);
    assert.ok(!(await page.locator('#weak-list').innerText()).includes('模型评估'));
    assert.equal(await page.locator('.material-detail').count(), 3);
    await page.locator('.material-detail').first().locator('summary').click();
    const firstNote = page.getByLabel('大模型基础.md的学习笔记');
    assert.equal(await firstNote.inputValue(), '原有笔记');
    await firstNote.fill('自动保存的新笔记');
    await page.waitForTimeout(400);
    assert.equal(noteWrites.length, 0);
    await page.waitForFunction(() => document.querySelector('.note-save-status').textContent.includes('已保存'));
    assert.deepEqual(noteWrites.at(-1), { id: 'm1', content: '自动保存的新笔记' });
    const heightBefore = await firstNote.evaluate(element => element.getBoundingClientRect().height);
    await page.getByLabel('大模型基础.md笔记显示高度').fill('12');
    const heightAfter = await firstNote.evaluate(element => element.getBoundingClientRect().height);
    assert.ok(heightAfter > heightBefore * 1.5);
    await firstNote.fill('第一次输入');
    await page.waitForTimeout(950);
    await firstNote.fill('第二次输入');
    await page.waitForFunction(() => document.querySelector('.note-save-status').textContent.includes('已保存') && document.querySelector('.material-note').value === '第二次输入');
    assert.equal(notes.get('m1'), '第二次输入');
    failNextNote = true;
    await firstNote.fill('失败后修改的笔记');
    await page.waitForFunction(() => document.querySelector('.note-save-status').textContent.includes('保存失败'));
    await firstNote.fill('失败后再次输入');
    await page.waitForFunction(() => document.querySelector('.note-save-status').textContent.includes('已保存'));
    assert.equal(notes.get('m1'), '失败后再次输入');
    await firstNote.fill('切页前立即保存');
    await page.locator('[data-route="learn"]').click();
    await page.waitForFunction(() => document.querySelector('.note-save-status').textContent.includes('已保存'));
    assert.equal(notes.get('m1'), '切页前立即保存');
    await page.locator('[data-route="progress"]').click();
    await page.screenshot({ path: path.join(os.tmpdir(), 'codex-progress-desktop.png'), fullPage: true });
    failSummary = true;
    await page.locator('#progress-refresh').click();
    await page.waitForFunction(() => !document.getElementById('progress-error').hidden);
    assert.match(await page.locator('#progress-error').innerText(), /统计服务暂不可用/);
    assert.equal(await page.locator('.knowledge-item').count(), 4);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: path.join(os.tmpdir(), 'codex-progress-mobile.png'), fullPage: true });
    assert.deepEqual(pageErrors, []);
    console.log('PASS: summary metrics, paged knowledge/material data, accessible unassessed state, radar, knowledge/weak lists, XSS-safe text, note debounce/sequencing/failure recovery/route flush, note height slider, partial API failure, mobile layout.');
    console.log('Screenshots: ' + path.join(os.tmpdir(), 'codex-progress-desktop.png') + ' | ' + path.join(os.tmpdir(), 'codex-progress-mobile.png'));
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
