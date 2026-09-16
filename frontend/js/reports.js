/* 报告页只排版后台保存的统计快照与 AI 汇总，不在前端推断学习结论。 */
window.reportsPage = (() => {
  const $ = id => document.getElementById(id);
  const api = window.api.reports;
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  let active = false;
  let mode = 'idle';
  let busy = false;
  let viewing = false;
  let payload = null;
  let reportId = null;
  let currentReport = null;
  let historyLoading = false;
  let historyLoaded = false;
  let historyPage = 1;
  let historyTotal = 0;

  function node(tag, className, text) {
    const value = document.createElement(tag);
    if (className) value.className = className;
    if (text !== undefined) value.textContent = text;
    return value;
  }

  function dateValue(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  }

  function parseDate(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
    if (!match) return null;
    const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 12);
    return date.getFullYear() === Number(match[1]) && date.getMonth() === Number(match[2]) - 1 && date.getDate() === Number(match[3]) ? date : null;
  }

  function thisWeek() {
    const now = new Date();
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - ((now.getDay() + 6) % 7), 12);
    const end = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 6, 12);
    return { from: dateValue(start), to: dateValue(end) };
  }

  function displayDate(value) {
    const date = parseDate(value);
    return date ? date.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' }) : value;
  }

  function displayTime(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '时间未记录' : date.toLocaleString('zh-CN');
  }

  function rate(value) {
    if (!Number.isFinite(value)) return '—';
    const rounded = Math.round(value * 10) / 10;
    return `${Number.isInteger(rounded) ? rounded : rounded.toFixed(1)}%`;
  }

  function currentRange() {
    const periodType = document.querySelector('[name="report-period"]:checked').value;
    if (periodType === 'week') return { period_type: 'week', date_from: thisWeek().from, date_to: thisWeek().to };
    return { period_type: 'custom', date_from: $('report-date-from').value, date_to: $('report-date-to').value };
  }

  function updateRangeUI() {
    const range = currentRange();
    const custom = range.period_type === 'custom';
    $('report-custom-range').hidden = !custom;
    $('report-week-range').hidden = custom;
    $('report-week-range').textContent = `本周：${displayDate(thisWeek().from)}—${displayDate(thisWeek().to)}`;
    $('report-date-from').max = $('report-date-to').value;
    $('report-date-to').min = $('report-date-from').value;
  }

  function controls() {
    $('report-settings').disabled = mode === 'generating';
    $('report-generate').disabled = busy || viewing;
    $('report-generate').classList.toggle('is-loading', busy && mode === 'generating');
    $('report-generate').textContent = busy && mode === 'generating' ? '正在生成学习报告...' : mode === 'generating' ? '继续获取报告' : '生成报告 ✦';
    $('report-generate').setAttribute('aria-busy', String(busy && mode === 'generating'));
    $('report-download').disabled = !currentReport || busy || viewing;
    $('report-history-refresh').disabled = historyLoading || busy || viewing;
    $('report-history-prev').disabled = historyLoading || busy || viewing || historyPage <= 1;
    $('report-history-next').disabled = historyLoading || busy || viewing || historyPage * 10 >= historyTotal;
    $('report-history-list').querySelectorAll('button').forEach(button => { button.disabled = busy || viewing; });
  }

  function showError(message) {
    $('report-error').textContent = message;
    $('report-error').hidden = false;
  }

  function clearError() {
    $('report-error').hidden = true;
  }

  function validStringList(value) {
    return Array.isArray(value) && value.every(item => typeof item === 'string');
  }

  function validateReport(report) {
    const stats = report?.statistics;
    const content = report?.content;
    const average = stats?.average_score_rate;
    if (!report?.report_id || report.status !== 'ready' || !parseDate(report.date_from) || !parseDate(report.date_to) || report.date_from > report.date_to || typeof report.timezone !== 'string' || !report.timezone || typeof report.generated_at !== 'string' || typeof report.data_cutoff_at !== 'string' || !stats || ['uploads_count', 'questions_count', 'assessments_count'].some(key => !Number.isInteger(stats[key]) || stats[key] < 0) || !(average === null || Number.isFinite(average) && average >= 0 && average <= 100) || !content || typeof content.title !== 'string' || !content.title.trim() || typeof content.summary !== 'string' || !validStringList(content.learned) || !validStringList(content.weak_points) || !validStringList(content.next_week_suggestions)) throw new Error('报告数据格式不完整，请重新获取。');
  }

  function renderList(target, values, emptyText) {
    target.replaceChildren();
    const items = values.length ? values : [emptyText];
    for (const value of items) target.append(node('li', values.length ? '' : 'subtle', value));
  }

  function renderReport(report) {
    validateReport(report);
    currentReport = report;
    $('report-empty').hidden = true;
    $('report-content').hidden = false;
    $('report-view-title').textContent = report.content.title;
    $('report-period-label').textContent = `${displayDate(report.date_from)}—${displayDate(report.date_to)} · ${report.timezone}`;
    $('report-upload-count').textContent = report.statistics.uploads_count;
    $('report-question-count').textContent = report.statistics.questions_count;
    $('report-assessment-count').textContent = report.statistics.assessments_count;
    $('report-average-score').textContent = rate(report.statistics.average_score_rate);
    $('report-summary').textContent = report.content.summary || '这段时间暂无学习记录。';
    renderList($('report-learned'), report.content.learned, '暂无可归纳的学习内容。');
    renderList($('report-weak-points'), report.content.weak_points, '暂无足够的测评数据判断薄弱知识点。');
    renderList($('report-suggestions'), report.content.next_week_suggestions, '继续积累学习和测评记录。');
    $('report-generated-at').textContent = `生成时间：${displayTime(report.generated_at)}`;
    $('report-data-cutoff').textContent = `数据截止：${displayTime(report.data_cutoff_at)}`;
    controls();
  }

  async function pollReport() {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      const report = await api.get(reportId);
      if (report?.status === 'ready') return report;
      if (report?.status === 'generation_failed') {
        const error = new Error(report.error_message || '报告生成失败，请调整日期后重试。');
        error.terminal = true;
        throw error;
      }
      if (report?.status !== 'generating') throw new Error('后台返回的报告状态不正确，请继续获取。');
      await new Promise(resolve => setTimeout(resolve, 1500));
    }
    throw new Error('报告仍在生成，可继续获取；不会重复创建报告。');
  }

  async function generateReport() {
    if (!reportId) {
      let created;
      try { created = await api.create(payload); }
      catch (error) {
        if ([400, 404, 422].includes(error.status)) error.terminal = true;
        throw error;
      }
      if (!created?.report_id || !['generating', 'ready'].includes(created.status)) throw new Error('未确认报告是否创建，可继续使用原请求获取。');
      reportId = created.report_id;
    }
    return pollReport();
  }

  async function runGeneration() {
    if (busy || viewing) return;
    busy = true;
    clearError();
    controls();
    try {
      const report = await generateReport();
      renderReport(report);
      mode = 'idle';
      payload = null;
      reportId = null;
      historyPage = 1;
      historyLoaded = false;
      await loadHistory(true);
      if (active) $('report-viewer').scrollIntoView({ block: 'start' });
    } catch (error) {
      if (error.terminal) {
        mode = 'idle';
        payload = null;
        reportId = null;
      }
      showError(error.message);
    } finally {
      busy = false;
      controls();
    }
  }

  function validateHistory(data) {
    if (!data || !Array.isArray(data.items) || !Number.isInteger(data.total) || data.total < 0) throw new Error('历史报告响应格式不正确');
    for (const item of data.items) if (!item.id || !parseDate(item.date_from) || !parseDate(item.date_to) || item.date_from > item.date_to || typeof item.title !== 'string' || typeof item.generated_at !== 'string') throw new Error('历史报告条目格式不正确');
  }

  async function loadHistory(force = false) {
    if (historyLoading || historyLoaded && !force) return;
    historyLoading = true;
    controls();
    $('report-history-message').hidden = false;
    $('report-history-message').textContent = '正在读取历史报告…';
    $('report-history-list').replaceChildren();
    try {
      const data = await api.history(historyPage);
      validateHistory(data);
      historyTotal = data.total;
      historyLoaded = true;
      $('report-history-page').textContent = `第 ${historyPage} / ${Math.max(1, Math.ceil(historyTotal / 10))} 页`;
      $('report-history-message').hidden = Boolean(data.items.length);
      $('report-history-message').textContent = '还没有生成过报告。';
      for (const item of data.items) {
        const row = node('article', 'report-history-row');
        const info = document.createElement('div');
        info.append(node('h3', '', item.title), node('p', '', `${displayDate(item.date_from)}—${displayDate(item.date_to)} · ${displayTime(item.generated_at)}`));
        const button = node('button', 'button button-secondary button-small', '查看报告');
        button.type = 'button';
        button.setAttribute('aria-label', `查看报告 ${item.title}`);
        button.onclick = () => viewReport(item.id);
        row.append(info, button);
        $('report-history-list').append(row);
      }
    } catch (error) {
      $('report-history-message').textContent = `读取历史报告失败：${error.message}`;
      historyLoaded = false;
    } finally {
      historyLoading = false;
      controls();
    }
  }

  async function viewReport(id) {
    if (busy || viewing) return;
    viewing = true;
    clearError();
    controls();
    try {
      const report = await api.get(id);
      renderReport(report);
      if (active) $('report-viewer').scrollIntoView({ block: 'start' });
    } catch (error) { showError(`打开报告失败：${error.message}`); }
    finally { viewing = false; controls(); }
  }

  async function downloadReport() {
    if (!currentReport) return;
    clearError();
    try {
      const blob = await api.download(currentReport.report_id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `学习报告_${currentReport.date_from}_${currentReport.date_to}.md`;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (error) {
      showError(`下载报告失败：${error.message}`);
    }
  }

  const week = thisWeek();
  $('report-date-from').value = week.from;
  $('report-date-to').value = week.to;
  $('report-timezone').textContent = `本地时区 · ${timezone}`;
  updateRangeUI();
  document.querySelectorAll('[name="report-period"]').forEach(input => input.addEventListener('change', updateRangeUI));
  for (const input of [$('report-date-from'), $('report-date-to')]) input.addEventListener('change', updateRangeUI);
  $('report-generator').addEventListener('submit', event => {
    event.preventDefault();
    if (busy || viewing) return;
    if (mode === 'generating') { runGeneration(); return; }
    const range = currentRange();
    const from = parseDate(range.date_from);
    const to = parseDate(range.date_to);
    if (!from || !to || from > to) {
      showError('请选择有效的开始和结束日期，开始日期不能晚于结束日期。');
      return;
    }
    payload = { request_id: crypto.randomUUID(), ...range, timezone };
    reportId = null;
    mode = 'generating';
    runGeneration();
  });
  $('report-download').onclick = downloadReport;
  $('report-history-refresh').onclick = () => { historyLoaded = false; loadHistory(true); };
  $('report-history-prev').onclick = () => { historyPage -= 1; historyLoaded = false; loadHistory(true); };
  $('report-history-next').onclick = () => { historyPage += 1; historyLoaded = false; loadHistory(true); };

  return {
    setActive(value) {
      active = value;
      if (active) loadHistory();
    }
  };
})();
