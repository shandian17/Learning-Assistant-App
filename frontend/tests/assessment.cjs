// 在本地浏览器中拦截模拟 API；不会接触真实资料或成绩。
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
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const papers = new Map();
    const creates = [];
    const submissions = [];
    let submitNetworkFailure = false;
    let gradingFailure = false;
    let generationFailure = false;
    let generationNetworkFailure = false;
    let retryCount = 0;
    let historyReady = false;
    let sourcesFailure = false;
    const materialScope = [{ material_id: 'm1', version_id: 'v1', filename: '大模型基础.md' }];
    const questionsFor = counts => Object.entries(counts).flatMap(([type, count]) => Array.from({ length: count }, (_, index) => ({
      id: `${type}-${index}`, type, difficulty: ['easy', 'medium', 'hard'][index % 3], max_score: 1,
      stem: type === 'short_answer' ? '解释资料中注意力机制的作用。' : type === 'true_false' ? '模型输出一定正确。' : '以下哪一项与资料中的概念一致？',
      options: type === 'single_choice'
        ? index === 0
          ? JSON.stringify({ A: '资料选项 A', B: '资料选项 B', C: '资料选项 C', D: '资料选项 D' })
          : ['A', 'B', 'C', 'D'].map(id => ({ id, text: `资料选项 ${id}` }))
        : type === 'true_false' ? { ignored: '前端必须使用固定选项' } : {},
      correct_answer: 'SECRET_NOT_FOR_ANSWER_SCREEN'
    })));
    const resultFor = (paper, duration = 65) => {
      const questions = paper.questions.map((question, index) => ({
        ...question,
        answer: paper.submission?.answers.find(answer => answer.question_id === question.id)?.answer ?? null,
        correct_answer: question.type === 'single_choice' ? 'A' : question.type === 'true_false' ? false : '通过相关性权重聚合上下文信息。',
        score: question.type === 'short_answer' ? .5 : index === 1 ? 0 : 1,
        feedback: question.type === 'short_answer' ? '方向正确，但缺少按相关性加权的要点。' : '依据资料核对后的评分。'
      }));
      return { status: 'graded', total_score: questions.reduce((sum, question) => sum + question.score, 0), max_score: questions.length, duration_seconds: duration, questions, material_scope: materialScope };
    };
    await page.route('**/api/v1/**', async route => {
      const req = route.request();
      const url = new URL(req.url());
      const respond = (data, status = 200) => route.fulfill({ status, json: { data } });
      if (url.pathname.endsWith('/materials')) {
        if (sourcesFailure) return route.fulfill({ status: 503, json: { error: { message: '资料服务离线' } } });
        return respond({ items: [{ id: 'm1', filename: '大模型基础.md', status: 'ready' }, { id: 'm2', filename: '解析中.pdf', status: 'processing' }], total: 2 });
      }
      if (url.pathname === '/api/v1/assessments' && req.method() === 'GET') {
        const pageNumber = Number(url.searchParams.get('page'));
        return respond({ items: historyReady ? Array.from({ length: pageNumber === 1 ? 10 : 1 }, (_, index) => ({ id: 'a1', graded_at: '2026-09-16T10:00:00Z', total_score: 3.5, max_score: 5, duration_seconds: 65, material_scope: [{ filename: `历史测评 ${pageNumber}-${index + 1}` }] })) : [], total: historyReady ? 11 : 0 });
      }
      if (url.pathname === '/api/v1/assessments' && req.method() === 'POST') {
        const body = req.postDataJSON();
        creates.push(body);
        const existing = [...papers.entries()].find(([, paper]) => paper.request_id === body.request_id);
        if (existing) return respond({ assessment_id: existing[0], status: 'generating' }, 202);
        const id = `a${papers.size + 1}`;
        papers.set(id, { request_id: body.request_id, questions: questionsFor(body.question_counts), reads: 0, resultReads: 0 });
        return respond({ assessment_id: id, status: 'generating' }, 202);
      }
      const id = url.pathname.split('/')[4];
      const paper = papers.get(id);
      if (url.pathname.endsWith('/submissions')) {
        const body = req.postDataJSON();
        submissions.push(body);
        paper.submission = body;
        if (submitNetworkFailure) { submitNetworkFailure = false; return route.abort('failed'); }
        return respond({ submission_id: `s-${id}`, status: 'grading' }, 202);
      }
      if (url.pathname.endsWith('/retry-grading')) {
        retryCount += 1;
        gradingFailure = false;
        return respond({ submission_id: `s-${id}`, status: 'grading' }, 202);
      }
      if (url.pathname.endsWith('/result')) {
        if (gradingFailure) return respond({ status: 'grading_failed', error_message: '评分暂时失败，请重试。' });
        if (paper.resultReads++ === 0) return respond({ status: 'grading' });
        historyReady = true;
        return respond(resultFor(paper));
      }
      if (generationNetworkFailure) { generationNetworkFailure = false; return route.abort('failed'); }
      if (generationFailure) return respond({ status: 'generation_failed', error_message: '资料不足以生成试题' });
      if (paper.reads++ === 0) return respond({ status: 'generating' });
      return respond({ id, status: 'ready', material_scope: materialScope, questions: paper.questions });
    });

    await page.goto(`http://127.0.0.1:${server.address().port}/#assessment`);
    await page.getByLabel('大模型基础.md', { exact: true }).waitFor();
    assert.equal(await page.locator('#assessment-start').isDisabled(), true);
    assert.equal(await page.getByLabel('解析中.pdf', { exact: true }).count(), 0);
    for (const count of [8, 10, 15, 5]) {
      await page.locator(`[name="question-count"][value="${count}"]`).check();
      assert.match(await page.locator('#assessment-mix').innerText(), /选择/);
    }
    await page.getByLabel('大模型基础.md', { exact: true }).check();
    await page.screenshot({ path: path.join(os.tmpdir(), 'codex-assessment-setup.png'), fullPage: true });
    await page.locator('#assessment-start').click();
    assert.equal(await page.locator('#assessment-start').isDisabled(), true);
    assert.match(await page.locator('#assessment-start').innerText(), /正在生成测试题/);
    await page.locator('#assessment-setup').evaluate(form => form.dispatchEvent(new Event('submit', { cancelable: true })));
    await page.locator('#assessment-answer:not([hidden])').waitFor();
    assert.equal(creates.length, 1);
    assert.equal(creates[0].language, 'zh-CN');
    assert.deepEqual(creates[0].question_counts, { single_choice: 3, true_false: 1, short_answer: 1 });
    assert.equal(await page.locator('.question-card').count(), 5);
    assert.ok(!(await page.locator('#assessment-questions').innerText()).includes('SECRET_NOT_FOR_ANSWER_SCREEN'));
    const cards = page.locator('.question-card');
    await cards.nth(0).getByRole('radio').nth(0).check();
    await cards.nth(0).getByRole('radio').nth(1).check();
    assert.equal(await cards.nth(0).locator('input:checked').count(), 1);
    assert.equal(await cards.nth(0).locator('label:has(input:checked)').evaluate(el => getComputedStyle(el).borderTopColor), 'rgb(212, 183, 115)');
    await cards.nth(1).getByRole('radio').nth(0).check();
    await cards.nth(2).getByRole('radio').nth(0).check();
    await cards.nth(3).getByRole('radio', { name: 'B. 错误', exact: true }).check();
    await page.locator('#assessment-submit').click();
    await page.locator('#assessment-confirm[open]').waitFor();
    await page.getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(submissions.length, 0);
    await page.getByLabel('第 5 题简答').fill('根据上下文理解输入。');
    await page.waitForFunction(() => document.getElementById('assessment-timer').textContent !== '00:00');
    await page.locator('[data-route="learn"]').click();
    await page.locator('[data-route="assessment"]').click();
    assert.equal(await page.getByLabel('第 5 题简答').inputValue(), '根据上下文理解输入。');
    await page.locator('#assessment-submit').click();
    assert.equal(await page.locator('#assessment-submit').isDisabled(), true);
    assert.match(await page.locator('#assessment-submit').innerText(), /正在出结果/);
    await page.locator('#assessment-answer').evaluate(form => form.dispatchEvent(new Event('submit', { cancelable: true })));
    await page.locator('#assessment-result:not([hidden])').waitFor();
    assert.equal(submissions.length, 1);
    assert.equal(submissions[0].answers[3].answer, false);
    assert.ok(submissions[0].duration_seconds >= 1);
    await page.waitForFunction(() => document.getElementById('assessment-score').textContent === '3.5');
    assert.equal(await page.locator('#assessment-correct').innerText(), '3');
    assert.equal(await page.locator('#assessment-partial').innerText(), '1');
    assert.equal(await page.locator('#assessment-wrong').innerText(), '1');
    assert.equal(await page.locator('#assessment-duration').innerText(), '01:05');
    await page.locator('#assessment-review summary').last().click();
    assert.match(await page.locator('#assessment-review details').last().innerText(), /根据上下文理解输入/);
    assert.match(await page.locator('#assessment-review details').last().innerText(), /缺少按相关性加权/);
    await page.screenshot({ path: path.join(os.tmpdir(), 'codex-assessment-result.png'), fullPage: true });
    await page.locator('#assessment-history-next').click();
    await page.waitForFunction(() => document.getElementById('assessment-history-page').textContent.includes('第 2'));
    assert.equal(await page.locator('.history-row').count(), 1);
    await page.getByRole('button', { name: '查看结果', exact: true }).click();
    await page.waitForFunction(() => !document.getElementById('assessment-again').disabled);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.locator('#assessment-again').click();
    await page.getByLabel('大模型基础.md', { exact: true }).check();
    generationNetworkFailure = true;
    await page.locator('#assessment-start').click();
    await page.locator('#assessment-recover:not([hidden])').waitFor();
    const createCount = creates.length;
    await page.locator('#assessment-recover').click();
    await page.locator('#assessment-answer:not([hidden])').waitFor();
    assert.equal(creates.length, createCount);
    await page.locator('#assessment-abandon').click();
    await page.locator('#assessment-confirm-yes').click();
    await page.locator('#assessment-setup:not([hidden])').waitFor();
    assert.equal(submissions.length, 1);
    await page.getByLabel('大模型基础.md', { exact: true }).check();
    await page.locator('#assessment-start').click();
    await page.locator('#assessment-answer:not([hidden])').waitFor();
    submitNetworkFailure = true;
    gradingFailure = true;
    await page.locator('#assessment-submit').click();
    await page.locator('#assessment-confirm-yes').click();
    await page.locator('#assessment-recover:not([hidden])').waitFor();
    assert.equal(await page.locator('#assessment-submit').isDisabled(), true);
    await page.locator('#assessment-recover').click();
    await page.waitForFunction(() => document.getElementById('assessment-recover').textContent === '重新评分');
    assert.deepEqual(submissions.at(-1), submissions.at(-2));
    assert.equal(submissions.at(-1).confirm_unanswered, true);
    assert.ok(submissions.at(-1).answers.every(answer => answer.answer === null));
    await page.locator('#assessment-recover').click();
    await page.locator('#assessment-result:not([hidden])').waitFor();
    assert.equal(retryCount, 1);
    await page.locator('#assessment-again').click();
    await page.getByLabel('大模型基础.md', { exact: true }).check();
    generationFailure = true;
    await page.locator('#assessment-start').click();
    await page.locator('#assessment-error:not([hidden])').waitFor();
    assert.match(await page.locator('#assessment-error-text').innerText(), /资料不足/);
    assert.equal(await page.locator('#assessment-setup').isVisible(), true);
    sourcesFailure = true;
    await page.locator('#assessment-refresh-sources').click();
    await page.waitForFunction(() => document.getElementById('assessment-sources').textContent.includes('资料服务离线'));
    assert.equal(await page.locator('#assessment-start').isDisabled(), true);
    assert.deepEqual(errors, []);
    console.log('PASS: source gating, question counts, generation lock/recovery, three question types, gold selection, boolean answers, timer, route retention, unanswered confirmation, abandon, submission idempotency, grading retry, score/review, history pagination, mobile layout, failure states.');
    console.log('Screenshots: ' + path.join(os.tmpdir(), 'codex-assessment-setup.png') + ' | ' + path.join(os.tmpdir(), 'codex-assessment-result.png'));
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
