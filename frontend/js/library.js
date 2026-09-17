/* 资料库交互独立于路由；所有服务请求集中在 api.js。 */
window.library = (() => {
  const $ = id => document.getElementById(id);
  const t = window.i18n.t;
  const zone = $('upload-zone');
  const input = $('material-files');
  const rows = $('material-rows');
  const dialog = $('duplicate-dialog');
  const types = { pdf: 'PDF', ppt: 'PPT', pptx: 'PPT', doc: 'Word', docx: 'Word', md: 'Markdown', markdown: 'Markdown', txt: 'TXT' };
  const statuses = { processing: '解析中', ready: '完成', failed: '失败' };
  const busyIds = new Set();
  let items = [];
  let loaded = false;
  let active = false;
  let uploading = false;
  let loading = null;
  let pollTimer;
  let dragDepth = 0;

  function notify(message, error = false) {
    const toast = document.createElement('div');
    toast.className = `toast${error ? ' error' : ''}`;
    const text = document.createElement('p');
    text.textContent = message;
    const close = document.createElement('button');
    close.type = 'button';
    close.textContent = '×';
    close.setAttribute('aria-label', t('关闭提示'));
    close.onclick = () => toast.remove();
    toast.append(text, close);
    $('notifications').append(toast);
    setTimeout(() => toast.remove(), error ? 15000 : 8000);
  }

  function stateOf(item) { return item.pending_version?.status || item.status; }
  function sizeOf(size) {
    if (typeof size !== 'number' || !Number.isFinite(size) || size < 0) return '—';
    if (size < 1024) return `${size} B`;
    const unit = size < 1024 ** 2 ? 'KB' : size < 1024 ** 3 ? 'MB' : 'GB';
    return `${(size / 1024 ** ({ KB: 1, MB: 2, GB: 3 }[unit])).toFixed(1)} ${unit}`;
  }

  function render() {
    rows.replaceChildren();
    $('material-count').textContent = loaded ? t('· {count} 份', { count: items.length }) : '';
    $('material-table-wrap').hidden = !items.length;
    $('library-empty').hidden = Boolean(items.length);
    if (loaded && !items.length) {
      $('library-empty-title').textContent = t('还没有学习资料');
      $('library-empty-description').textContent = t('上传第一份课程资料，从这里开始积累。');
    }
    for (const item of items) {
      const row = document.createElement('tr');
      const filename = document.createElement('td');
      filename.textContent = item.filename;
      const state = stateOf(item);
      const error = item.pending_version?.error_message || item.error_message;
      if (state === 'failed' && error) {
        const detail = document.createElement('p');
        detail.className = 'file-error';
        detail.textContent = window.i18n.message(error, '资料解析失败，请重试。');
        filename.append(detail);
      }
      if (item.pending_version && item.current_version_id) {
        const note = document.createElement('p');
        note.className = 'file-note';
        note.textContent = t('当前显示新上传版本状态，原版本仍可使用');
        filename.append(note);
      }
      const type = document.createElement('td');
      const extension = item.filename.trim().split('.').pop().toLowerCase();
      type.textContent = types[extension] || item.file_type || '—';
      const size = document.createElement('td');
      size.textContent = sizeOf(item.pending_version?.file_size ?? item.file_size);
      const status = document.createElement('td');
      const badge = document.createElement('span');
      badge.className = `material-status ${Object.hasOwn(statuses, state) ? state : ''}`;
      badge.textContent = Object.hasOwn(statuses, state) ? t(statuses[state]) : t('状态未知');
      status.append(badge);
      const actions = document.createElement('td');
      const group = document.createElement('div');
      group.className = 'row-actions';
      for (const action of state === 'failed' ? ['retry', 'delete'] : ['delete']) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `button button-small ${action === 'delete' ? 'button-danger' : 'button-secondary'}`;
        button.textContent = action === 'retry' ? t('重试') : t('删除');
        button.setAttribute('aria-label', `${button.textContent} ${item.filename}`);
        button.disabled = busyIds.has(item.id) || uploading;
        button.onclick = () => actOnItem(item, action);
        group.append(button);
      }
      actions.append(group);
      row.append(filename, type, size, status, actions);
      rows.append(row);
    }
  }

  function scheduleRefresh() {
    clearTimeout(pollTimer);
    if (active && !document.hidden) {
      pollTimer = setTimeout(refresh, items.some(item => stateOf(item) === 'processing') ? 4000 : 15000);
    }
  }

  function refresh() {
    if (loading) return loading;
    clearTimeout(pollTimer);
    $('refresh-materials').disabled = true;
    loading = (async () => {
      try {
        items = await window.api.materials.list();
        loaded = true;
        $('library-error').hidden = true;
        render();
      } catch (error) {
        $('library-error').textContent = t('读取资料失败：{message}{suffix}', { message: error.message, suffix: loaded ? t('。当前显示上次读取的列表。') : '' });
        $('library-error').hidden = false;
        if (!loaded) {
          $('library-empty-title').textContent = t('暂时无法读取资料');
          $('library-empty-description').textContent = t('请连接后台后点击“刷新列表”重试。');
        }
      } finally {
        $('refresh-materials').disabled = false;
        loading = null;
        scheduleRefresh();
      }
    })();
    return loading;
  }

  async function refreshAfterMutation() {
    // 等待可能仍在返回旧列表的请求，再读取本次操作后的状态。
    if (loading) await loading;
    await refresh();
  }

  function chooseDuplicate(filename) {
    $('duplicate-description').textContent = t('资料库中已存在“{filename}”，请选择如何处理。', { filename });
    dialog.returnValue = 'cancel';
    return new Promise(resolve => {
      dialog.addEventListener('close', () => resolve(dialog.returnValue), { once: true });
      dialog.showModal();
    });
  }

  async function uploadOne(file) {
    const extension = file.name.trim().split('.').pop().toLowerCase();
    if (!Object.hasOwn(types, extension)) throw new Error(t('格式不支持，请选择 PDF、PPT、Word、Markdown 或 TXT 文件'));
    if (!file.size) throw new Error(t('文件为空，请选择有内容的资料'));
    // 冲突时重新检查并重新询问，绝不默认覆盖已经变化的版本。
    for (;;) {
      const check = await window.api.materials.checkName(file.name);
      if (!check || typeof check.duplicate !== 'boolean') throw new Error(t('同名检查响应格式不正确'));
      const choice = { language: window.i18n.language };
      if (check.duplicate) {
        const existing = check.existing_material;
        if (!existing?.id) throw new Error(t('无法读取同名资料信息，请刷新后重试'));
        const action = await chooseDuplicate(file.name);
        if (!['replace', 'keep_both'].includes(action)) return false;
        choice.duplicate_action = action;
        if (action === 'replace') {
          choice.target_material_id = existing.id;
          choice.expected_version_id = existing.current_version_id ?? '';
        }
      }
      try {
        const result = await window.api.materials.upload(file, choice);
        if (!result?.material_id || !Object.hasOwn(statuses, result.status)) throw new Error(t('上传响应无法确认，请刷新列表核对，勿重复上传'));
        if (result.status === 'failed') notify(t('“{filename}”已上传，但解析失败，可在列表中重试。', { filename: file.name }), true);
        else notify(t(result.status === 'ready' ? '“{filename}”上传成功，解析完成。' : '“{filename}”上传成功，正在解析。', { filename: file.name }));
        return true;
      } catch (error) {
        if (error.status === 409 && ['DUPLICATE_NAME', 'VERSION_CONFLICT'].includes(error.code)) {
          notify(t('同名资料发生变化，请重新选择处理方式。'), true);
          continue;
        }
        throw error;
      }
    }
  }

  async function uploadFiles(files) {
    if (uploading || !files.length) return;
    uploading = true;
    zone.disabled = true;
    zone.setAttribute('aria-busy', 'true');
    render();
    try {
      for (const [index, file] of Array.from(files).entries()) {
        $('upload-status').textContent = t('正在上传 {current} / {total}：{filename}', { current: index + 1, total: files.length, filename: file.name });
        try { await uploadOne(file); }
        catch (error) { notify(t('“{filename}”上传未完成：{message}', { filename: file.name, message: error.message }), true); }
        await refreshAfterMutation();
      }
    } finally {
      uploading = false;
      zone.disabled = false;
      zone.removeAttribute('aria-busy');
      input.value = '';
      $('upload-status').textContent = t('点击此区域选择文件，可一次上传多份');
      render();
    }
  }

  async function actOnItem(item, action) {
    if (busyIds.has(item.id) || uploading) return;
    busyIds.add(item.id);
    render();
    try {
      if (action === 'retry') {
        await window.api.materials.retry(item.id);
        notify(t('“{filename}”已提交重新解析。', { filename: item.filename }));
      } else {
        await window.api.materials.remove(item.id);
        items = items.filter(value => value.id !== item.id);
        notify(t('“{filename}”已删除。', { filename: item.filename }));
      }
      await refreshAfterMutation();
    } catch (error) {
      notify(t('{action}失败：{message}', { action: t(action === 'retry' ? '重试' : '删除'), message: error.message }), true);
    } finally {
      busyIds.delete(item.id);
      render();
    }
  }

  zone.onclick = () => input.click();
  input.onchange = () => uploadFiles(Array.from(input.files));
  zone.addEventListener('dragenter', event => {
    event.preventDefault();
    dragDepth += 1;
    if (!uploading) zone.classList.add('is-dragging');
  });
  zone.addEventListener('dragover', event => {
    event.preventDefault();
    event.dataTransfer.dropEffect = uploading ? 'none' : 'copy';
  });
  zone.addEventListener('dragleave', () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) zone.classList.remove('is-dragging');
  });
  zone.addEventListener('drop', event => {
    event.preventDefault();
    dragDepth = 0;
    zone.classList.remove('is-dragging');
    uploadFiles(Array.from(event.dataTransfer.files));
  });
  // 在资料库拖出上传框时也阻止浏览器直接打开文件。
  for (const type of ['dragover', 'drop']) document.addEventListener(type, event => {
    if (active && Array.from(event.dataTransfer?.types || []).includes('Files')) event.preventDefault();
  });
  $('refresh-materials').onclick = refresh;
  document.addEventListener('visibilitychange', () => {
    if (active && !document.hidden) refresh();
    else clearTimeout(pollTimer);
  });

  return {
    setActive(value) {
      active = value;
      if (active) refresh();
      else clearTimeout(pollTimer);
    }
  };
})();
