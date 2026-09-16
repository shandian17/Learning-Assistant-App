/* 测评页面状态与交互；出题、判分和历史数据均来自后台。 */
window.assessment = (() => {
  const $ = id => document.getElementById(id);
  const api = window.api.assessments;
  const typeNames = { single_choice: '选择', true_false: '判断', short_answer: '简答' };
  const difficultyNames = { easy: '简单', medium: '中等', hard: '困难' };
  const trueFalseOptions = Object.freeze({ A: '正确', B: '错误' });
  const distributions = {
    5: { single_choice: 3, true_false: 1, short_answer: 1 },
    8: { single_choice: 4, true_false: 2, short_answer: 2 },
    10: { single_choice: 5, true_false: 3, short_answer: 2 },
    15: { single_choice: 8, true_false: 4, short_answer: 3 }
  };
  let stage = 'setup';
  let active = false;
  let busy = false;
  let confirming = false;
  let recovery = null;
  let sourcesLoading = false;
  let historyLoading = false;
  let historyPage = 1;
  let historyTotal = 0;
  let createPayload = null;
  let assessmentId = null;
  let questions = [];
  let answers = new Map();
  let startedAt = 0;
  let stoppedDuration = null;
  let timer;
  let animationFrame;
  let submitPayload = null;
  let submissionAccepted = false;
  let resultScope = [];

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function durationText(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return '未记录';
    const value = Math.floor(seconds);
    return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
  }

  function scopeText(scope) {
    return Array.isArray(scope) ? scope.map(item => typeof item === 'string' ? item : item.filename).filter(Boolean).join('、') : '';
  }

  function optionObject(value, type) {
    if (type === 'true_false') return { ...trueFalseOptions };
    if (type === 'short_answer') return {};
    let parsed = value;
    if (typeof parsed === 'string') {
      try { parsed = JSON.parse(parsed); }
      catch { throw new Error('选择题选项不是合法 JSON，请重新获取。'); }
    }
    if (Array.isArray(parsed)) {
      parsed = Object.fromEntries(parsed.map(option => [option?.id, option?.text]));
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('选择题选项格式不正确，请重新获取。');
    if (Object.values(parsed).some(text => typeof text !== 'string')) throw new Error('选择题选项内容格式不正确，请重新获取。');
    const options = Object.fromEntries(Object.entries(parsed).map(([id, text]) => [String(id), text]));
    if (!['A', 'B', 'C', 'D'].every(id => Object.hasOwn(options, id)) || Object.keys(options).length !== 4 || Object.values(options).some(text => !text.trim())) throw new Error('选择题必须包含 A、B、C、D 四个选项。');
    return Object.fromEntries(['A', 'B', 'C', 'D'].map(id => [id, options[id]]));
  }

  function selectedIds() { return Array.from($('assessment-sources').querySelectorAll('input:checked'), input => input.value); }
  function counts() { return distributions[$('assessment-settings').querySelector('[name="question-count"]:checked').value]; }
  function answered(question) {
    const value = answers.get(question.id);
    return typeof value === 'string' ? Boolean(value.trim()) : typeof value === 'boolean';
  }

  function controls() {
    const generating = stage === 'generating';
    $('assessment-settings').disabled = generating;
    $('assessment-start').disabled = stage !== 'setup' || busy || sourcesLoading || !selectedIds().length;
    $('assessment-start').classList.toggle('is-loading', generating && busy);
    $('assessment-start').textContent = generating ? (busy ? '正在生成测试题...' : '等待继续生成') : '开始测试 ↗';
    $('assessment-start').setAttribute('aria-busy', String(generating && busy));
    const locked = stage !== 'answer' || busy || confirming;
    $('assessment-questions').querySelectorAll('fieldset').forEach(fieldset => { fieldset.disabled = locked; });
    $('assessment-submit').disabled = locked;
    $('assessment-abandon').disabled = locked;
    $('assessment-submit').textContent = stage === 'grading' ? (busy ? '正在出结果...' : '等待获取结果') : '提交答案';
    $('assessment-submit').classList.toggle('is-loading', stage === 'grading' && busy);
    $('assessment-submit').setAttribute('aria-busy', String(stage === 'grading' && busy));
    $('assessment-recover').disabled = busy;
    $('assessment-again').disabled = busy;
    $('assessment-refresh-history').disabled = historyLoading || busy;
    $('assessment-history-prev').disabled = historyLoading || busy || historyPage <= 1;
    $('assessment-history-next').disabled = historyLoading || busy || historyPage * 10 >= historyTotal;
    $('assessment-history-list').querySelectorAll('button').forEach(button => { button.disabled = busy; });
  }

  function setStage(value) {
    stage = value;
    $('assessment-setup').hidden = !['setup', 'generating'].includes(stage);
    $('assessment-answer').hidden = !['answer', 'grading'].includes(stage);
    $('assessment-result').hidden = stage !== 'result';
    $('assessment-history').hidden = !['setup', 'result'].includes(stage);
    const step = ['generating', 'setup'].includes(stage) ? 'setup' : stage === 'result' ? 'result' : 'answer';
    document.querySelectorAll('[data-assessment-step]').forEach(item => {
      if (item.dataset.assessmentStep === step) item.setAttribute('aria-current', 'step');
      else item.removeAttribute('aria-current');
    });
    controls();
  }

  function clearError() {
    $('assessment-error').hidden = true;
    $('assessment-recover').hidden = true;
    recovery = null;
  }

  async function run(action, retryLabel) {
    if (busy) return;
    busy = true;
    clearError();
    controls();
    try { await action(); }
    catch (error) {
      $('assessment-error-text').textContent = error.message;
      $('assessment-error').hidden = false;
      recovery = error.terminal ? null : error.recover || action;
      $('assessment-recover').hidden = !recovery;
      $('assessment-recover').textContent = error.recoverLabel || retryLabel || '重试';
    } finally {
      busy = false;
      controls();
    }
  }

  async function loadSources() {
    if (sourcesLoading || stage !== 'setup') return;
    sourcesLoading = true;
    const selected = new Set(selectedIds());
    $('assessment-refresh-sources').disabled = true;
    controls();
    try {
      const items = await window.api.materials.list();
      const ready = items.filter(item => item.status === 'ready' || item.current_version?.status === 'ready' || (item.pending_version && item.current_version_id));
      $('assessment-sources').replaceChildren();
      for (const item of ready) {
        const label = element('label', 'assessment-source');
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.value = item.id;
        input.checked = selected.has(item.id);
        label.append(input, element('span', '', item.filename));
        $('assessment-sources').append(label);
      }
      if (!ready.length) $('assessment-sources').append(element('p', 'subtle', '暂无可用资料，请先在资料库上传并完成解析。'));
    } catch (error) {
      $('assessment-sources').replaceChildren(element('p', 'subtle', `读取资料失败：${error.message}`));
    } finally {
      sourcesLoading = false;
      $('assessment-refresh-sources').disabled = false;
      controls();
    }
  }

  async function poll(read, pendingStatus, completeStatus) {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      const data = await read();
      if (data?.status === completeStatus) return data;
      if (data?.status === 'generation_failed') {
        setStage('setup');
        assessmentId = null;
        const error = new Error(data.error_message || '题目生成失败，请调整资料后重新开始。');
        error.terminal = true;
        throw error;
      }
      if (data?.status === 'grading_failed') {
        const error = new Error(data.error_message || '评分失败，原答案已保留，可重新评分。');
        error.recover = retryGrading;
        error.recoverLabel = '重新评分';
        throw error;
      }
      if (data?.status !== pendingStatus) throw new Error('后台返回的测评状态不正确，请重新获取。');
      await new Promise(resolve => setTimeout(resolve, 1500));
    }
    throw new Error('后台仍在处理中，可继续获取；不会重复创建试卷或提交答案。');
  }

  function validateQuestions(data) {
    const expected = createPayload.question_counts;
    if (!Array.isArray(data.questions) || data.questions.length !== Object.values(expected).reduce((sum, value) => sum + value, 0)) throw new Error('返回题数与所选题数不一致，请重新获取。');
    const ids = new Set();
    const normalized = data.questions.map(question => ({ ...question, options: optionObject(question.options, question.type) }));
    for (const question of normalized) {
      if (!question.id || ids.has(question.id) || !Object.hasOwn(typeNames, question.type) || typeof question.stem !== 'string' || question.max_score !== 1) throw new Error('试题格式不完整，请重新获取。');
      ids.add(question.id);
    }
    for (const type of Object.keys(expected)) if (normalized.filter(question => question.type === type).length !== expected[type]) throw new Error('试题题型数量不符合本次设置，请重新获取。');
    return normalized;
  }

  async function generate() {
    if (!assessmentId) {
      let created;
      try { created = await api.create(createPayload); }
      catch (error) {
        if ([400, 404, 415, 422].includes(error.status)) { error.terminal = true; setStage('setup'); }
        throw error;
      }
      if (!created?.assessment_id) throw new Error('未收到测评编号，请重试确认本次生成结果。');
      assessmentId = created.assessment_id;
    }
    const data = await poll(() => api.get(assessmentId), 'generating', 'ready');
    const normalizedQuestions = validateQuestions(data);
    // 只取答题所需字段，绝不把后台意外返回的标准答案渲染到作答区。
    questions = normalizedQuestions.map(({ id, type, difficulty, stem, options, max_score }) => ({ id, type, difficulty, stem, options, max_score }));
    resultScope = data.material_scope || [];
    answers = new Map();
    startedAt = Date.now();
    stoppedDuration = null;
    renderQuestions();
    setStage('answer');
    tick();
    clearInterval(timer);
    timer = setInterval(tick, 1000);
  }

  function tick() { $('assessment-timer').textContent = durationText(stoppedDuration ?? Math.floor((Date.now() - startedAt) / 1000)); }
  function updateProgress(index) {
    const completed = questions.filter(answered).length;
    if (index !== undefined) $('assessment-current').textContent = `第 ${index + 1} / ${questions.length} 题`;
    $('assessment-answered').textContent = `已答 ${completed} / ${questions.length}`;
    $('assessment-progress').max = questions.length;
    $('assessment-progress').value = completed;
  }

  function renderQuestions() {
    $('assessment-questions').replaceChildren();
    questions.forEach((question, index) => {
      const card = element('article', 'panel question-card');
      const meta = element('div', 'question-meta');
      meta.append(element('span', 'subtle', `QUESTION ${String(index + 1).padStart(2, '0')}`), element('span', 'tag', typeNames[question.type]), element('span', 'difficulty-tag', difficultyNames[question.difficulty] || '难度未标注'));
      const fieldset = document.createElement('fieldset');
      const legend = element('legend', 'question-stem', `${index + 1}. ${question.stem}`);
      fieldset.append(legend);
      if (question.type === 'short_answer') {
        const textarea = element('textarea', 'short-answer');
        textarea.rows = 5;
        textarea.placeholder = '用自己的话写下你的理解…';
        textarea.setAttribute('aria-label', `第 ${index + 1} 题简答`);
        textarea.addEventListener('input', () => { answers.set(question.id, textarea.value); updateProgress(index); });
        fieldset.append(textarea);
      } else {
        const options = Object.entries(question.options).map(([id, text]) => ({ id, text }));
        const optionList = element('div', 'answer-options');
        options.forEach(option => {
          const label = element('label', 'answer-option');
          const input = document.createElement('input');
          input.type = 'radio';
          input.name = `question-${index}`;
          input.value = option.id;
          input.addEventListener('change', () => { answers.set(question.id, question.type === 'true_false' ? option.id === 'A' : option.id); updateProgress(index); });
          label.append(input, element('span', '', `${option.id}. ${option.text}`));
          optionList.append(label);
        });
        fieldset.append(optionList);
      }
      card.addEventListener('focusin', () => updateProgress(index));
      card.append(meta, fieldset);
      $('assessment-questions').append(card);
    });
    updateProgress(0);
  }

  async function confirmAction(title, text, label) {
    const dialog = $('assessment-confirm');
    $('assessment-confirm-title').textContent = title;
    $('assessment-confirm-text').textContent = text;
    $('assessment-confirm-yes').textContent = label;
    dialog.returnValue = 'cancel';
    confirming = true;
    controls();
    const value = await new Promise(resolve => {
      dialog.addEventListener('close', () => resolve(dialog.returnValue), { once: true });
      dialog.showModal();
    });
    confirming = false;
    controls();
    return value === 'confirm';
  }

  async function submit() {
    if (!submissionAccepted) {
      const response = await api.submit(assessmentId, submitPayload);
      if (!response?.submission_id) throw new Error('尚未确认提交结果，可使用原提交重试。');
      submissionAccepted = true;
    }
    await obtainResult();
  }

  async function obtainResult() {
    const result = await poll(() => api.result(assessmentId), 'grading', 'graded');
    renderResult(result, stoppedDuration);
    setStage('result');
    historyPage = 1;
    await loadHistory();
  }

  async function retryGrading() {
    try { await api.retryGrading(assessmentId); }
    catch (error) { if (error.status !== 409) throw error; }
    await obtainResult();
  }

  function answerText(question, value) {
    if (value === null || value === undefined || value === '') return '未作答';
    if (question.type === 'true_false') return value === true ? '正确' : value === false ? '错误' : String(value);
    if (question.type === 'single_choice') {
      const option = question.options?.[value];
      return option ? `${value}. ${option}` : String(value);
    }
    return String(value);
  }

  function renderResult(result, localDuration = null) {
    const list = Array.isArray(result.questions)
      ? result.questions.map(question => ({ ...question, options: optionObject(question.options, question.type) }))
      : result.questions;
    if (!Array.isArray(list) || !list.length || !Number.isFinite(result.total_score) || result.max_score !== list.length || new Set(list.map(question => question.id)).size !== list.length || list.some(question => !question.id || typeof question.stem !== 'string' || typeof question.feedback !== 'string' || !Object.hasOwn(question, 'correct_answer') || !Object.hasOwn(typeNames, question.type) || !(question.type === 'short_answer' ? [0, .5, 1] : [0, 1]).includes(question.score)) || list.reduce((sum, question) => sum + question.score, 0) !== result.total_score) throw new Error('成绩数据不完整或不一致，请重新获取。');
    const full = list.filter(question => question.score === 1).length;
    const partial = list.filter(question => question.score === .5).length;
    $('assessment-correct').textContent = full;
    $('assessment-partial').textContent = partial;
    $('assessment-wrong').textContent = list.length - full - partial;
    $('assessment-duration').textContent = durationText(result.duration_seconds ?? localDuration);
    $('assessment-max-score').textContent = `/ ${result.max_score} 分`;
    $('assessment-result-scope').textContent = scopeText(result.material_scope || (localDuration !== null ? resultScope : []));
    $('assessment-score-chart').setAttribute('aria-label', `得分 ${result.total_score}，满分 ${result.max_score}`);
    $('assessment-review').replaceChildren();
    list.forEach((question, index) => {
      const card = element('details', 'panel review-card');
      const summary = document.createElement('summary');
      summary.append(element('span', '', `第 ${index + 1} 题`), element('span', 'tag', typeNames[question.type]), element('span', '', question.stem), element('span', 'review-score', ` · ${question.score} / 1 分`));
      const content = element('div', 'review-content');
      const values = document.createElement('dl');
      for (const [label, value] of [['正确答案', answerText(question, question.correct_answer)], ['自己的答案', answerText(question, question.answer)], ['AI 点评', question.feedback || '暂无点评']]) {
        values.append(element('dt', '', label), element('dd', '', value));
      }
      content.append(values);
      card.append(summary, content);
      $('assessment-review').append(card);
    });
    animateScore(result.total_score, result.max_score);
  }

  function animateScore(score, maximum) {
    cancelAnimationFrame(animationFrame);
    const start = performance.now();
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const frame = now => {
      const progress = reduced ? 1 : Math.min(1, (now - start) / 900);
      const eased = 1 - (1 - progress) ** 3;
      $('assessment-score-ring').style.strokeDashoffset = 100 - score / maximum * 100 * eased;
      $('assessment-score').textContent = progress === 1 ? String(score) : (score * eased).toFixed(1);
      if (progress < 1) animationFrame = requestAnimationFrame(frame);
    };
    animationFrame = requestAnimationFrame(frame);
  }

  async function loadHistory() {
    if (historyLoading) return;
    historyLoading = true;
    controls();
    $('assessment-history-message').hidden = false;
    $('assessment-history-message').textContent = '正在读取历史记录…';
    // 切页时移除旧页，避免旧记录被误认为新页。
    $('assessment-history-list').replaceChildren();
    try {
      const data = await api.history(historyPage);
      if (!Array.isArray(data?.items) || !Number.isInteger(data.total) || data.total < 0) throw new Error('历史记录响应格式不正确');
      historyTotal = data.total;
      $('assessment-history-page').textContent = `第 ${historyPage} / ${Math.max(1, Math.ceil(historyTotal / 10))} 页`;
      $('assessment-history-message').hidden = Boolean(data.items.length);
      $('assessment-history-message').textContent = '还没有完成的测评，完成后会在这里保留成绩。';
      for (const item of data.items) {
        const row = element('div', 'history-row');
        const info = document.createElement('div');
        const date = new Date(item.graded_at);
        info.append(element('p', '', scopeText(item.material_scope) || '课程测评'), element('p', 'subtle', `${Number.isNaN(date.getTime()) ? '时间未记录' : date.toLocaleString('zh-CN')} · ${item.total_score} / ${item.max_score} 分 · ${durationText(item.duration_seconds)}`));
        const button = element('button', 'button button-secondary button-small', '查看结果');
        button.type = 'button';
        button.onclick = () => run(async () => {
          const result = await api.result(item.id);
          if (result?.status !== 'graded') throw new Error('这份测评的结果暂不可用。');
          renderResult(result);
          setStage('result');
          if (active) $('assessment-result').scrollIntoView({ block: 'start' });
        }, '重新读取结果');
        row.append(info, button);
        $('assessment-history-list').append(row);
      }
    } catch (error) { $('assessment-history-message').textContent = `读取历史记录失败：${error.message}`; }
    finally { historyLoading = false; controls(); }
  }

  function reset() {
    clearInterval(timer);
    cancelAnimationFrame(animationFrame);
    assessmentId = null;
    createPayload = null;
    submitPayload = null;
    submissionAccepted = false;
    questions = [];
    answers.clear();
    resultScope = [];
    clearError();
    setStage('setup');
    loadSources();
    loadHistory();
  }

  $('assessment-setup').addEventListener('submit', event => {
    event.preventDefault();
    if (busy || stage !== 'setup' || !selectedIds().length) return;
    createPayload = { request_id: crypto.randomUUID(), material_ids: selectedIds(), question_counts: { ...counts() } };
    assessmentId = null;
    setStage('generating');
    run(generate, '继续获取题目');
  });
  $('assessment-sources').addEventListener('change', controls);
  $('assessment-settings').addEventListener('change', () => {
    const mix = counts();
    $('assessment-mix').textContent = `选择 ${mix.single_choice} 道 · 判断 ${mix.true_false} 道 · 简答 ${mix.short_answer} 道`;
  });
  $('assessment-answer').addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || confirming || stage !== 'answer') return;
    const unanswered = questions.filter(question => !answered(question)).length;
    if (unanswered && !await confirmAction('还有题目未作答', `有 ${unanswered} 道题未作答，提交后这些题将计为 0 分。`, '仍然提交')) return;
    stoppedDuration = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
    clearInterval(timer);
    tick();
    submitPayload = {
      request_id: crypto.randomUUID(),
      answers: questions.map(question => ({ question_id: question.id, answer: answered(question) ? answers.get(question.id) : null })),
      confirm_unanswered: unanswered > 0,
      duration_seconds: stoppedDuration
    };
    setStage('grading');
    run(submit, '继续获取结果');
  });
  $('assessment-abandon').onclick = async () => {
    if (busy || confirming || stage !== 'answer') return;
    if (await confirmAction('放弃本次测评？', '本次未提交的答案将清空，不会生成成绩。以前的测评记录仍然保留。', '放弃测评')) reset();
  };
  $('assessment-again').onclick = () => { if (!busy) reset(); };
  $('assessment-refresh-sources').onclick = loadSources;
  $('assessment-refresh-history').onclick = loadHistory;
  $('assessment-history-prev').onclick = () => { historyPage -= 1; loadHistory(); };
  $('assessment-history-next').onclick = () => { historyPage += 1; loadHistory(); };
  $('assessment-recover').onclick = () => { if (recovery) run(recovery, $('assessment-recover').textContent); };
  window.addEventListener('beforeunload', event => {
    if (['answer', 'generating', 'grading'].includes(stage)) { event.preventDefault(); event.returnValue = ''; }
  });
  return {
    setActive(value) {
      active = value;
      if (active && stage === 'setup') { loadSources(); loadHistory(); }
      else if (active && stage === 'result') loadHistory();
    }
  };
})();
