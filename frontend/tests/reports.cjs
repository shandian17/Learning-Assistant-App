// 在本地浏览器中拦截模拟 API；不会读取真实学习记录或创建真实报告。
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
    const page = await browser.newPage({ viewport: { width: 1280, height: 1000 }, acceptDownloads: true });
    await page.addInitScript(() => localStorage.setItem('learning-assistant-language', 'zh-CN'));
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    const reports = new Map();
    const createCalls = [];
    let failCreateOnce = false;
    let historyAvailable = false;
    let failHistory = false;
    const buildReport = (id, request, suffix = '') => ({
      report_id: id,
      status: 'ready',
      period_type: request.period_type,
      date_from: request.date_from,
      date_to: request.date_to,
      timezone: request.timezone,
      data_cutoff_at: '2026-09-16T15:00:00Z',
      generated_at: '2026-09-16T15:01:00Z',
      statistics: { uploads_count: 2, questions_count: 7, assessments_count: 3, average_score_rate: 82.5 },
      content: {
        title: `大模型课程学习报告${suffix}`,
        summary: '本期围绕模型结构和提示词设计进行了学习，并完成了三次测评。',
        learned: ['理解了 Transformer 的基本结构', '<img src=x onerror=alert(1)> 不应被执行'],
        weak_points: ['注意力机制中的权重计算仍需巩固'],
        next_week_suggestions: ['复习注意力机制相关资料', '完成一次针对薄弱知识点的测评']
      }
    });
    await page.route('**/api/v1/**', async route => {
      const req = route.request();
      const url = new URL(req.url());
      const respond = (data, status = 200) => route.fulfill({ status, json: { data } });
      if (url.pathname === '/api/v1/materials' && req.method() === 'GET') return respond({ items: [], total: 0 });
      if (url.pathname === '/api/v1/weekly-reports' && req.method() === 'POST') {
        const body = req.postDataJSON();
        createCalls.push(body);
        let entry = [...reports.entries()].find(([, report]) => report.request_id === body.request_id);
        if (!entry) {
          const id = `r${reports.size + 1}`;
          reports.set(id, { request_id: body.request_id, request: body, reads: 0 });
          entry = [id, reports.get(id)];
        }
        if (failCreateOnce) {
          failCreateOnce = false;
          return route.abort('failed');
        }
        return respond({ report_id: entry[0], status: 'generating' }, 202);
      }
      if (url.pathname === '/api/v1/weekly-reports' && req.method() === 'GET') {
        if (failHistory) return route.fulfill({ status: 503, json: { error: { message: '历史服务暂不可用' } } });
        const pageNumber = Number(url.searchParams.get('page'));
        const all = historyAvailable ? Array.from({ length: 11 }, (_, index) => ({ id: `h${index + 1}`, title: `历史学习报告 ${index + 1}`, date_from: '2026-08-03', date_to: '2026-08-09', generated_at: '2026-08-10T08:00:00Z' })) : [];
        return respond({ items: all.slice((pageNumber - 1) * 10, pageNumber * 10), total: all.length });
      }
      if (/\/api\/v1\/weekly-reports\/[^/]+\/download$/.test(url.pathname) && req.method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'text/markdown; charset=utf-8',
          headers: {
            'Content-Disposition': "attachment; filename=report.md; filename*=UTF-8''%E5%AD%A6%E4%B9%A0%E6%8A%A5%E5%91%8A_2026-09-14_2026-09-20.md"
          },
          body: '# 大模型课程学习报告\n\n## 学到了什么\n\n- &lt;img src=x onerror=alert(1)&gt;\n\n## 薄弱知识点\n\n- 注意力机制\n\n## 下一步建议\n\n1. 复习资料\n'
        });
      }
      const id = url.pathname.split('/').pop();
      if (/^h\d+$/.test(id)) return respond(buildReport(id, { period_type: 'week', date_from: '2026-08-03', date_to: '2026-08-09', timezone: 'America/Toronto' }, ` ${id.slice(1)}`));
      const stored = reports.get(id);
      if (stored) {
        if (stored.reads++ === 0) return respond({ report_id: id, status: 'generating' });
        historyAvailable = true;
        return respond(buildReport(id, stored.request));
      }
      return route.fulfill({ status: 404, json: { error: { message: '报告不存在' } } });
    });

    await page.goto(`http://127.0.0.1:${server.address().port}/#reports`);
    await page.waitForFunction(() => document.getElementById('report-history-message').textContent.includes('还没有'));
    assert.match(await page.locator('#report-week-range').innerText(), /本周/);
    assert.match(await page.locator('#report-timezone').innerText(), /本地时区/);
    await page.locator('#report-generate').click();
    assert.equal(await page.locator('#report-generate').isDisabled(), true);
    assert.match(await page.locator('#report-generate').innerText(), /正在生成学习报告/);
    await page.locator('#report-generator').evaluate(form => form.dispatchEvent(new Event('submit', { cancelable: true })));
    await page.locator('#report-content:not([hidden])').waitFor();
    assert.equal(createCalls.length, 1);
    assert.equal(createCalls[0].period_type, 'week');
    assert.equal(createCalls[0].language, 'zh-CN');
    assert.ok(createCalls[0].request_id);
    assert.ok(createCalls[0].timezone);
    assert.ok(createCalls[0].date_from <= createCalls[0].date_to);
    assert.equal(await page.locator('#report-upload-count').innerText(), '2');
    assert.equal(await page.locator('#report-question-count').innerText(), '7');
    assert.equal(await page.locator('#report-assessment-count').innerText(), '3');
    assert.equal(await page.locator('#report-average-score').innerText(), '82.5%');
    assert.equal(await page.locator('.report-body img').count(), 0);
    assert.match(await page.locator('#report-learned').innerText(), /<img src=x onerror=alert\(1\)>/);
    assert.equal(await page.locator('#report-learned li').count(), 2);
    assert.equal(await page.locator('#report-weak-points li').count(), 1);
    assert.equal(await page.locator('#report-suggestions li').count(), 2);
    const downloadPromise = page.waitForEvent('download');
    await page.locator('#report-download').click();
    const download = await downloadPromise;
    assert.match(download.suggestedFilename(), /^学习报告_\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}\.md$/);
    const downloadedPath = await download.path();
    const markdown = fs.readFileSync(downloadedPath, 'utf8');
    assert.match(markdown, /^# 大模型课程学习报告/m);
    assert.match(markdown, /## 学到了什么/);
    assert.match(markdown, /## 薄弱知识点/);
    assert.match(markdown, /## 下一步建议/);
    assert.ok(markdown.includes('&lt;img src=x onerror=alert'));
    await page.screenshot({ path: path.join(os.tmpdir(), 'codex-report-desktop.png'), fullPage: true });
    await page.locator('#report-history-next').click();
    await page.waitForFunction(() => document.getElementById('report-history-page').textContent.includes('第 2'));
    assert.equal(await page.locator('.report-history-row').count(), 1);
    await page.getByRole('button', { name: '查看报告 历史学习报告 11', exact: true }).click();
    await page.waitForFunction(() => document.getElementById('report-view-title').textContent.includes('11'));
    assert.match(await page.locator('#report-period-label').innerText(), /2026年8月3日/);
    await page.locator('[name="report-period"][value="custom"]').check();
    assert.equal(await page.locator('#report-custom-range').isVisible(), true);
    await page.locator('#report-date-from').fill('2026-07-01');
    await page.locator('#report-date-to').fill('2026-07-20');
    failCreateOnce = true;
    await page.locator('#report-generate').click();
    await page.waitForFunction(() => !document.getElementById('report-error').hidden);
    assert.match(await page.locator('#report-generate').innerText(), /继续获取报告/);
    const callsBeforeRetry = createCalls.length;
    const retryRequestId = createCalls.at(-1).request_id;
    await page.locator('#report-generate').click();
    await page.locator('#report-content:not([hidden])').waitFor();
    await page.waitForFunction(() => document.getElementById('report-period-label').textContent.includes('2026年7月20日'));
    assert.equal(createCalls.length, callsBeforeRetry + 1);
    assert.equal(createCalls.at(-1).request_id, retryRequestId);
    assert.equal(createCalls.at(-1).period_type, 'custom');
    assert.equal(createCalls.at(-1).date_from, '2026-07-01');
    assert.equal(createCalls.at(-1).date_to, '2026-07-20');
    failHistory = true;
    await page.locator('#report-history-refresh').click();
    await page.waitForFunction(() => document.getElementById('report-history-message').textContent.includes('历史服务暂不可用'));
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: path.join(os.tmpdir(), 'codex-report-mobile.png'), fullPage: true });
    assert.deepEqual(pageErrors, []);
    console.log('PASS: this-week/custom ranges, generation lock, idempotent retry, structured report, XSS-safe rendering, Markdown download, history pagination/open, failure state, mobile layout.');
    console.log('Screenshots: ' + path.join(os.tmpdir(), 'codex-report-desktop.png') + ' | ' + path.join(os.tmpdir(), 'codex-report-mobile.png'));
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
