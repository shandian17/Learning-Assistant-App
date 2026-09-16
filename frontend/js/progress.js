/* 学习进度只展示后台基于完整测评计算的数据；未测评不折算为 0% 掌握。 */
window.progressPage = (() => {
  const $ = id => document.getElementById(id);
  const api = window.api.progress;
  const levels = {
    unassessed: { label: '未测评', className: 'unassessed' },
    needs_review: { label: '待巩固', className: 'needs_review' },
    partial: { label: '部分掌握', className: 'partial' },
    mastered: { label: '已掌握', className: 'mastered' }
  };
  const noteStates = new Map();
  let active = false;
  let loading = false;
  let loaded = false;

  function node(tag, className, text) {
    const value = document.createElement(tag);
    if (className) value.className = className;
    if (text !== undefined) value.textContent = text;
    return value;
  }

  function percent(value) {
    if (!Number.isFinite(value)) return '—';
    const rounded = Math.round(value * 10) / 10;
    return `${Number.isInteger(rounded) ? rounded : rounded.toFixed(1)}%`;
  }

  function validateSummary(data) {
    const integers = ['total_materials', 'mastered_materials', 'assessments_count', 'weak_points_count'];
    if (!data || integers.some(key => !Number.isInteger(data[key]) || data[key] < 0) || data.mastered_materials > data.total_materials || !(data.average_score_rate === null || Number.isFinite(data.average_score_rate) && data.average_score_rate >= 0 && data.average_score_rate <= 100)) throw new Error('学习统计响应格式不正确');
  }

  function validatePoints(items) {
    for (const item of items) {
      if (!item.knowledge_point_id || typeof item.name !== 'string' || !Object.hasOwn(levels, item.mastery_status)) throw new Error('知识点数据格式不正确');
      if (item.mastery_status === 'unassessed') {
        if (item.latest_score !== null) throw new Error('未测评知识点不能包含得分');
      } else if (!Number.isFinite(item.latest_score) || item.latest_score < 0 || item.latest_score > 1) throw new Error('知识点得分格式不正确');
    }
  }

  function validateMaterials(items) {
    for (const item of items) {
      const counts = ['knowledge_points_total', 'assessed_points', 'mastered_points', 'partial_points', 'needs_review_points'];
      if (!item.material_id || typeof item.filename !== 'string' || counts.some(key => !Number.isInteger(item[key]) || item[key] < 0) || item.assessed_points > item.knowledge_points_total || item.mastered_points + item.partial_points + item.needs_review_points !== item.assessed_points || !(item.coverage_rate === null || Number.isFinite(item.coverage_rate) && item.coverage_rate >= 0 && item.coverage_rate <= 100) || !(item.mastery_rate === null || Number.isFinite(item.mastery_rate) && item.mastery_rate >= 0 && item.mastery_rate <= 100) || !item.note || typeof item.note.content !== 'string') throw new Error('资料进度数据格式不正确');
    }
  }

  function renderSummary(data) {
    $('progress-total-materials').textContent = data.total_materials;
    $('progress-mastered-materials').textContent = data.mastered_materials;
    $('progress-assessments').textContent = data.assessments_count;
    $('progress-average-score').textContent = percent(data.average_score_rate);
    $('progress-weak-count').textContent = data.weak_points_count;
  }

  function scoreFor(point) {
    return point.mastery_status === 'unassessed' ? null : point.latest_score * 100;
  }

  function renderKnowledge(items) {
    $('knowledge-count').textContent = `${items.length} 个`;
    $('knowledge-list').replaceChildren();
    if (!items.length) {
      $('knowledge-list').append(node('p', 'progress-placeholder', '暂无可统计知识点。'));
      return;
    }
    for (const point of items) {
      const item = node('article', 'knowledge-item');
      const heading = node('div', 'knowledge-heading');
      const title = document.createElement('div');
      title.append(node('h3', '', point.name), node('p', '', point.filename || '资料名称未提供'));
      const badge = node('span', `level-badge ${levels[point.mastery_status].className}`, levels[point.mastery_status].label);
      heading.append(title, badge);
      const row = node('div', 'knowledge-progress-row');
      const bar = node('div', 'knowledge-progress');
      bar.setAttribute('role', 'progressbar');
      bar.setAttribute('aria-label', `${point.name}掌握程度`);
      const value = scoreFor(point);
      if (value === null) bar.setAttribute('aria-valuetext', '未测评');
      else {
        bar.setAttribute('aria-valuemin', '0');
        bar.setAttribute('aria-valuemax', '100');
        bar.setAttribute('aria-valuenow', String(Math.round(value)));
      }
      const fill = node('span', levels[point.mastery_status].className);
      fill.style.width = `${value ?? 0}%`;
      bar.append(fill);
      const output = document.createElement('output');
      output.textContent = percent(value);
      row.append(bar, output);
      item.append(heading, row);
      $('knowledge-list').append(item);
    }
  }

  function svgNode(tag, attributes = {}) {
    const value = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, item] of Object.entries(attributes)) value.setAttribute(key, item);
    return value;
  }

  function polygonPoints(count, radius, centerX, centerY) {
    return Array.from({ length: count }, (_, index) => {
      const angle = -Math.PI / 2 + index * Math.PI * 2 / count;
      return [centerX + Math.cos(angle) * radius, centerY + Math.sin(angle) * radius];
    });
  }

  function renderRadar(items) {
    const chart = $('radar-chart');
    chart.replaceChildren();
    if (!items.length) {
      chart.append(node('p', 'subtle', '暂无可统计知识点。'));
      $('radar-subtitle').textContent = '上传并解析资料后显示';
      return;
    }
    const assessed = items.filter(item => item.mastery_status !== 'unassessed');
    $('radar-subtitle').textContent = assessed.length ? `${assessed.length} / ${items.length} 个知识点已有测评` : '全部知识点尚未测评';
    const svg = svgNode('svg', { viewBox: '0 0 600 520', role: 'img', 'aria-label': `知识点掌握雷达图，共 ${items.length} 个知识点，${assessed.length} 个已有测评` });
    const centerX = 300;
    const centerY = 250;
    const radius = items.length > 14 ? 165 : 180;
    const count = items.length;
    for (const ratio of [.25, .5, .75, 1]) {
      const points = polygonPoints(count, radius * ratio, centerX, centerY);
      svg.append(count < 3 ? svgNode('circle', { cx: centerX, cy: centerY, r: radius * ratio, class: 'radar-grid' }) : svgNode('polygon', { points: points.map(value => value.join(',')).join(' '), class: 'radar-grid' }));
      const label = svgNode('text', { x: centerX + 4, y: centerY - radius * ratio + 11, class: 'radar-scale' });
      label.textContent = `${ratio * 100}%`;
      svg.append(label);
    }
    const outer = polygonPoints(count, radius, centerX, centerY);
    outer.forEach(([x, y]) => svg.append(svgNode('line', { x1: centerX, y1: centerY, x2: x, y2: y, class: 'radar-axis' })));
    if (assessed.length) {
      const values = items.map((item, index) => {
        const value = scoreFor(item) ?? 0;
        const angle = -Math.PI / 2 + index * Math.PI * 2 / count;
        return [centerX + Math.cos(angle) * radius * value / 100, centerY + Math.sin(angle) * radius * value / 100];
      });
      if (count === 1) svg.append(svgNode('line', { x1: centerX, y1: centerY, x2: values[0][0], y2: values[0][1], class: 'radar-area' }));
      else if (count === 2) svg.append(svgNode('line', { x1: values[0][0], y1: values[0][1], x2: values[1][0], y2: values[1][1], class: 'radar-area' }));
      else svg.append(svgNode('polygon', { points: values.map(value => value.join(',')).join(' '), class: 'radar-area' }));
      values.forEach(([x, y], index) => svg.append(svgNode('circle', { cx: x, cy: y, r: 4, class: `radar-point${items[index].mastery_status === 'unassessed' ? ' unassessed' : ''}` })));
    } else {
      const message = svgNode('text', { x: centerX, y: centerY + 4, class: 'radar-empty-label' });
      message.textContent = '暂无测评数据';
      svg.append(message);
    }
    const fontSize = Math.max(8, Math.min(12, 150 / count));
    polygonPoints(count, radius + 42, centerX, centerY).forEach(([x, y], index) => {
      const angle = -Math.PI / 2 + index * Math.PI * 2 / count;
      const label = svgNode('text', { x, y, class: 'radar-label', 'font-size': fontSize, 'text-anchor': Math.abs(Math.cos(angle)) < .2 ? 'middle' : Math.cos(angle) > 0 ? 'start' : 'end', 'dominant-baseline': 'middle' });
      const shortName = items[index].name.length > 10 ? `${items[index].name.slice(0, 9)}…` : items[index].name;
      label.textContent = shortName;
      const title = svgNode('title');
      title.textContent = `${items[index].name}：${items[index].mastery_status === 'unassessed' ? '未测评' : percent(scoreFor(items[index]))}`;
      label.append(title);
      svg.append(label);
    });
    chart.append(svg);
  }

  function renderWeak(items) {
    const weak = items.filter(item => ['partial', 'needs_review'].includes(item.mastery_status));
    $('weak-count').textContent = `${weak.length} 个`;
    $('weak-list').replaceChildren();
    if (!weak.length) {
      $('weak-list').append(node('p', 'progress-placeholder', items.length ? '当前没有薄弱知识点。未测评知识点不计入薄弱项。' : '暂无可统计知识点。'));
      return;
    }
    for (const point of weak) {
      const item = node('article', 'weak-item');
      const header = document.createElement('header');
      header.append(node('h3', '', point.name), node('span', `level-badge ${levels[point.mastery_status].className}`, levels[point.mastery_status].label));
      item.append(header, node('p', '', `${point.filename || '资料名称未提供'} · ${percent(scoreFor(point))}`));
      $('weak-list').append(item);
    }
  }

  function noteStatus(state, message, className = '') {
    state.status.textContent = message;
    state.status.className = `note-save-status${className ? ` ${className}` : ''}`;
  }

  async function saveNote(state) {
    clearTimeout(state.timer);
    if (state.saving) {
      state.pending = true;
      return false;
    }
    const content = state.textarea.value;
    if (content === state.savedContent) return true;
    state.saving = true;
    state.pending = false;
    noteStatus(state, '正在自动保存…', 'is-saving');
    let succeeded = false;
    try {
      const result = await api.saveNote(state.materialId, { content });
      if (!result || result.material_id !== state.materialId || typeof result.content !== 'string' || result.content !== content || typeof result.updated_at !== 'string') throw new Error('笔记保存响应格式不正确');
      state.savedContent = result.content;
      succeeded = true;
      const time = new Date(result.updated_at);
      noteStatus(state, `已保存${Number.isNaN(time.getTime()) ? '' : ` · ${time.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`}`, 'is-saved');
      return true;
    } catch (error) {
      noteStatus(state, `保存失败：${error.message}，稍后自动重试`, 'is-error');
      return false;
    } finally {
      state.saving = false;
      if (state.pending || state.textarea.value !== state.savedContent) {
        clearTimeout(state.timer);
        if (succeeded) noteStatus(state, '等待自动保存');
        state.timer = setTimeout(() => saveNote(state), state.pending ? 0 : succeeded ? 900 : 5000);
      }
      state.pending = false;
    }
  }

  function renderMaterials(items) {
    for (const state of noteStates.values()) clearTimeout(state.timer);
    noteStates.clear();
    $('material-progress-list').replaceChildren();
    if (!items.length) {
      $('material-progress-list').append(node('div', 'panel progress-placeholder', '暂无资料详情。'));
      return;
    }
    for (const material of items) {
      const details = node('details', 'panel material-detail');
      const summary = document.createElement('summary');
      const intro = node('div', 'material-summary');
      const status = material.knowledge_points_total === 0 ? '暂无知识点' : material.assessed_points === 0 ? '尚未测评' : material.mastered_points === material.knowledge_points_total ? '当前版本已掌握' : `覆盖率 ${percent(material.coverage_rate)}`;
      intro.append(node('h3', '', material.filename), node('p', '', status));
      summary.append(intro);
      const body = node('div', 'material-detail-body');
      const metrics = node('div', 'material-metrics');
      for (const [value, label] of [[material.knowledge_points_total, '知识点'], [material.assessed_points, '已测评'], [material.mastered_points, '已掌握'], [percent(material.mastery_rate), '掌握率']]) {
        const item = document.createElement('div');
        item.append(node('strong', '', value), node('span', '', label));
        metrics.append(item);
      }
      const heading = node('div', 'note-heading');
      heading.append(node('h3', '', '学习笔记'));
      const saveStatus = node('span', 'note-save-status', material.note.updated_at ? '已保存' : '尚未保存');
      heading.append(saveStatus);
      const textarea = node('textarea', 'material-note');
      textarea.value = material.note.content;
      textarea.placeholder = '写下这份资料的重点、疑问或自己的理解…';
      textarea.setAttribute('aria-label', `${material.filename}的学习笔记`);
      textarea.style.setProperty('--note-lines', '5');
      const size = node('label', 'note-size-control');
      size.append(node('span', '', '缩略'));
      const range = document.createElement('input');
      range.type = 'range';
      range.min = '3';
      range.max = '12';
      range.value = '5';
      range.setAttribute('aria-label', `${material.filename}笔记显示高度`);
      range.addEventListener('input', () => textarea.style.setProperty('--note-lines', range.value));
      size.append(range, node('span', '', '展开'));
      const state = { materialId: material.material_id, textarea, status: saveStatus, savedContent: material.note.content, timer: null, saving: false, pending: false };
      noteStates.set(material.material_id, state);
      textarea.addEventListener('input', () => {
        clearTimeout(state.timer);
        noteStatus(state, '等待自动保存');
        state.timer = setTimeout(() => saveNote(state), 900);
      });
      body.append(metrics, heading, textarea, size);
      details.append(summary, body);
      $('material-progress-list').append(details);
    }
  }

  async function flushNotes() {
    const dirty = [...noteStates.values()].filter(state => state.textarea.value !== state.savedContent);
    if (!dirty.length) return true;
    const results = await Promise.all(dirty.map(saveNote));
    return results.every(Boolean);
  }

  async function load(force = false) {
    if (loading || loaded && !force) return;
    if (force && !await flushNotes()) {
      $('progress-error').textContent = '有笔记尚未保存，已保留当前内容并停止刷新。后台恢复后会自动重试。';
      $('progress-error').hidden = false;
      return;
    }
    loading = true;
    $('progress-refresh').disabled = true;
    $('progress-refresh').classList.add('is-loading');
    $('progress-refresh').textContent = '正在刷新';
    $('progress-error').hidden = true;
    const results = await Promise.allSettled([api.summary(), api.knowledgePoints(), api.materials()]);
    const errors = [];
    if (results[0].status === 'fulfilled') {
      try { validateSummary(results[0].value); renderSummary(results[0].value); }
      catch (error) { errors.push(error.message); }
    } else errors.push(`统计读取失败：${results[0].reason.message}`);
    if (results[1].status === 'fulfilled') {
      try { validatePoints(results[1].value); renderKnowledge(results[1].value); renderRadar(results[1].value); renderWeak(results[1].value); }
      catch (error) { errors.push(error.message); }
    } else errors.push(`知识点读取失败：${results[1].reason.message}`);
    if (results[2].status === 'fulfilled') {
      try { validateMaterials(results[2].value); renderMaterials(results[2].value); }
      catch (error) { errors.push(error.message); }
    } else errors.push(`资料详情读取失败：${results[2].reason.message}`);
    if (errors.length) {
      $('progress-error').textContent = errors.join('；');
      $('progress-error').hidden = false;
    }
    loaded = results.some(result => result.status === 'fulfilled');
    loading = false;
    $('progress-refresh').disabled = false;
    $('progress-refresh').classList.remove('is-loading');
    $('progress-refresh').textContent = '刷新进度';
  }

  $('progress-refresh').onclick = () => load(true);
  document.addEventListener('visibilitychange', () => { if (document.hidden) flushNotes(); });
  window.addEventListener('beforeunload', event => {
    if ([...noteStates.values()].some(state => state.textarea.value !== state.savedContent)) {
      event.preventDefault();
      event.returnValue = '';
    }
  });

  return {
    setActive(value) {
      active = value;
      if (active) load();
      else flushNotes();
    }
  };
})();
