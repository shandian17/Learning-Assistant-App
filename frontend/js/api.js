/* 默认与后台同源；分开部署时修改 index.html 中的 api-base。 */
window.api = (() => {
  const baseURL = document.querySelector('meta[name="api-base"]').content.replace(/\/$/, '');

  async function request(path, { method = 'GET', body, timeout = 5000 } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    const isForm = body instanceof FormData;
    try {
      const response = await fetch(`${baseURL}${path}`, {
        method,
        headers: { Accept: 'application/json', ...(body !== undefined && !isForm ? { 'Content-Type': 'application/json' } : {}) },
        body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
        signal: controller.signal,
        cache: 'no-store'
      });
      if (response.status === 204 && response.ok) return null;
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        const error = new Error(payload?.error?.message || `请求失败（${response.status}），请检查后台服务`);
        error.status = response.status;
        error.code = payload?.error?.code;
        error.details = payload?.error?.details;
        throw error;
      }
      if (!payload || typeof payload !== 'object' || !Object.hasOwn(payload, 'data')) {
        throw new Error('后台响应格式不正确');
      }
      return payload.data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('请求超时，操作结果暂无法确认，请刷新列表核对');
      if (error instanceof TypeError) throw new Error('无法连接后台，请检查服务和网络后重试');
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  async function checkConnection() {
    try {
      // 使用需求文档已有的只读接口，避免将静态网页响应误认为后台在线。
      const data = await request('/materials?page=1&page_size=1');
      return Boolean(data && Array.isArray(data.items) && Number.isInteger(data.total) && data.total >= 0);
    } catch {
      return false;
    }
  }

  const materials = {
    async list() {
      const items = [];
      for (let page = 1; ; page += 1) {
        const data = await request(`/materials?page=${page}&page_size=100`);
        if (!data || !Array.isArray(data.items) || !Number.isInteger(data.total) || data.total < 0) throw new Error('资料列表响应格式不正确');
        const ids = new Set(items.map(item => item.id));
        if (data.items.some(item => !item.id || ids.has(item.id))) throw new Error('资料列表分页异常，请刷新重试');
        items.push(...data.items);
        if (items.length >= data.total) return items;
        if (!data.items.length) throw new Error('资料列表不完整，请刷新重试');
      }
    },
    checkName: filename => request('/materials/check-name', { method: 'POST', body: { filename } }),
    upload(file, choice = {}) {
      const body = new FormData();
      body.append('file', file);
      Object.entries(choice).forEach(([key, value]) => body.append(key, value));
      return request('/materials', { method: 'POST', body, timeout: 120000 });
    },
    retry: id => request(`/materials/${encodeURIComponent(id)}/retry`, { method: 'POST' }),
    remove: id => request(`/materials/${encodeURIComponent(id)}`, { method: 'DELETE' })
  };

  const assessmentPath = id => `/assessments/${encodeURIComponent(id)}`;
  const assessments = {
    create: body => request('/assessments', { method: 'POST', body }),
    get: id => request(assessmentPath(id)),
    submit: (id, body) => request(`${assessmentPath(id)}/submissions`, { method: 'POST', body }),
    result: id => request(`${assessmentPath(id)}/result`),
    retryGrading: id => request(`${assessmentPath(id)}/retry-grading`, { method: 'POST' }),
    history: page => request(`/assessments?status=graded&page=${page}&page_size=10`)
  };

  const chatPath = id => `/chat/sessions/${encodeURIComponent(id)}`;
  const chats = {
    list: page => request(`/chat/sessions?page=${page}&page_size=20`),
    create: materialIds => request('/chat/sessions', { method: 'POST', body: { material_ids: materialIds } }),
    messages: (id, page = 1) => request(`${chatPath(id)}/messages?page=${page}&page_size=100`),
    send: (id, content) => request(`${chatPath(id)}/messages`, { method: 'POST', body: { content }, timeout: 390000 }),
    remove: id => request(chatPath(id), { method: 'DELETE' })
  };

  async function paged(path) {
    const items = [];
    for (let page = 1; ; page += 1) {
      const separator = path.includes('?') ? '&' : '?';
      const data = await request(`${path}${separator}page=${page}&page_size=100`);
      if (!data || !Array.isArray(data.items) || !Number.isInteger(data.total) || data.total < 0) throw new Error('分页响应格式不正确');
      const ids = new Set(items.map(item => item.id || item.knowledge_point_id || item.material_id));
      if (data.items.some(item => {
        const id = item.id || item.knowledge_point_id || item.material_id;
        if (!id || ids.has(id)) return true;
        ids.add(id);
        return false;
      })) throw new Error('分页数据重复，请刷新重试');
      items.push(...data.items);
      if (items.length >= data.total) return items;
      if (!data.items.length) throw new Error('分页数据不完整，请刷新重试');
    }
  }

  const progress = {
    summary: () => request('/progress/summary'),
    knowledgePoints: () => paged('/progress/knowledge-points'),
    materials: () => paged('/progress/materials'),
    saveNote(materialId, body) {
      return request(`/materials/${encodeURIComponent(materialId)}/note`, { method: 'PUT', body, timeout: 15000 });
    }
  };

  const reportPath = id => `/weekly-reports/${encodeURIComponent(id)}`;
  const reports = {
    create: body => request('/weekly-reports', { method: 'POST', body }),
    get: id => request(reportPath(id)),
    history: page => request(`/weekly-reports?status=ready&page=${page}&page_size=10`),
    async download(id) {
      let response;
      try {
        response = await fetch(`${baseURL}${reportPath(id)}/download`, {
          headers: { Accept: 'text/markdown' },
          cache: 'no-store'
        });
      } catch (error) {
        if (error instanceof TypeError) throw new Error('无法连接后台，请检查服务和网络后重试');
        throw error;
      }
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.error?.message || `下载失败（${response.status}）`);
      }
      return response.blob();
    }
  };

  return { request, checkConnection, materials, chats, assessments, progress, reports };
})();
