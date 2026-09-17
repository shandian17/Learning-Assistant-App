window.learnPage = (() => {
  const $ = id => document.getElementById(id);
  const t = window.i18n.t;
  const api = window.api.chats;
  let active = false;
  let loaded = false;
  let busy = false;
  let sessionId = null;

  function node(tag, className, text) {
    const value = document.createElement(tag);
    if (className) value.className = className;
    if (text !== undefined) value.textContent = text;
    return value;
  }

  function selectedIds() {
    return Array.from($('learn-sources').querySelectorAll('input:checked'), input => input.value);
  }

  function controls() {
    const selected = selectedIds();
    $('learn-refresh-sources').disabled = busy || Boolean(sessionId);
    $('learn-new-session').disabled = busy || !sessionId;
    $('learn-sources').querySelectorAll('input').forEach(input => { input.disabled = busy || Boolean(sessionId); });
    $('chat-input').disabled = busy || !selected.length;
    $('chat-send').disabled = busy || !selected.length || !$('chat-input').value.trim();
    $('chat-state').textContent = busy ? t('正在查找资料并生成回答…') : sessionId ? t('连续对话中') : selected.length ? t('已选择 {count} 份资料', { count: selected.length }) : t('请选择学习资料');
    $('chat-helper').textContent = busy ? t('正在生成回答，请稍候') : t('回答会标明资料出处');
  }

  async function loadSources(force = false) {
    if (busy || sessionId || loaded && !force) return;
    busy = true;
    $('learn-source-error').hidden = true;
    controls();
    const previous = new Set(selectedIds());
    try {
      const materials = await window.api.materials.list();
      const ready = materials.filter(item => item.status === 'ready' || item.current_version?.status === 'ready' || (item.pending_version && item.current_version_id));
      $('learn-sources').replaceChildren();
      for (const material of ready) {
        const label = node('label', 'learn-source');
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.value = material.id;
        input.setAttribute('aria-label', t('用于对话：{filename}', { filename: material.filename }));
        input.checked = previous.has(material.id);
        input.addEventListener('change', controls);
        label.append(input, node('span', '', material.filename));
        $('learn-sources').append(label);
      }
      if (!ready.length) {
        const empty = node('div', 'source-placeholder');
        empty.append(node('h3', '', t('还没有可用资料')), node('p', '', t('请先上传资料并等待解析完成。')));
        const link = node('a', 'button button-secondary', t('前往资料库 ↗'));
        link.href = '#library';
        empty.append(link);
        $('learn-sources').append(empty);
      }
      loaded = true;
    } catch (error) {
      $('learn-source-error').textContent = t('读取资料失败：{message}', { message: error.message });
      $('learn-source-error').hidden = false;
    } finally {
      busy = false;
      controls();
    }
  }

  function locatorText(locator) {
    if (!locator || typeof locator !== 'object') return '';
    if (locator.page !== undefined) return t('第 {value} 页', { value: locator.page });
    if (locator.slide !== undefined) return t('第 {value} 张', { value: locator.slide });
    if (locator.heading) return String(locator.heading);
    if (locator.line_start !== undefined) return t('第 {value} 行', { value: locator.line_start });
    return '';
  }

  function appendMessage(role, content, citations = []) {
    $('chat-welcome').hidden = true;
    const message = node('article', `chat-message ${role}`);
    message.append(node('div', '', content));
    if (role === 'assistant' && Array.isArray(citations) && citations.length) {
      const list = node('div', 'chat-citations');
      for (const citation of citations) {
        const location = locatorText(citation.locator);
        list.append(node('span', '', `${citation.filename || t('资料')}${location ? ` · ${location}` : ''}`));
      }
      message.append(list);
    }
    $('chat-messages').append(message);
    message.scrollIntoView({ block: 'end' });
  }

  async function sendMessage() {
    const content = $('chat-input').value.trim();
    const materialIds = selectedIds();
    if (busy || !content || !materialIds.length) return;
    busy = true;
    $('chat-error').hidden = true;
    controls();
    try {
      if (!sessionId) {
        const session = await api.create(materialIds);
        if (!session?.session_id) throw new Error(t('后台没有返回对话编号'));
        sessionId = session.session_id;
      }
      const response = await api.send(sessionId, content);
      const assistant = response?.assistant_message;
      if (!response?.user_message_id || !assistant?.id || typeof assistant.content !== 'string' || !Array.isArray(assistant.citations)) throw new Error(t('聊天响应格式不正确'));
      appendMessage('user', content);
      appendMessage('assistant', assistant.content, assistant.citations);
      $('chat-input').value = '';
    } catch (error) {
      $('chat-error').textContent = error.message;
      $('chat-error').hidden = false;
    } finally {
      busy = false;
      controls();
      if (!$('chat-input').disabled) $('chat-input').focus();
    }
  }

  function startNewSession() {
    if (busy) return;
    sessionId = null;
    $('chat-messages').replaceChildren();
    $('chat-welcome').hidden = false;
    $('chat-error').hidden = true;
    $('chat-input').value = '';
    controls();
  }

  $('chat-composer').addEventListener('submit', event => {
    event.preventDefault();
    sendMessage();
  });
  $('chat-input').addEventListener('input', controls);
  $('learn-refresh-sources').addEventListener('click', () => loadSources(true));
  $('learn-new-session').addEventListener('click', startNewSession);

  return {
    setActive(value) {
      active = value;
      if (active) loadSources();
    }
  };
})();
