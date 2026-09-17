(() => {
  const t = window.i18n.t;
  const routes = { learn: '学习', library: '资料库', assessment: '测评', progress: '进度', reports: '报告' };
  const pages = document.querySelectorAll('[data-page]');
  const links = document.querySelectorAll('[data-route]');
  const status = document.getElementById('connection-status');
  const statusLabel = document.getElementById('connection-label');
  let checking = false;
  let checkTimer;

  function renderRoute() {
    // 跳到主要内容的无障碍锚点不改变当前页面。
    if (location.hash === '#main-content') {
      document.getElementById('main-content').focus();
      return;
    }
    let route = location.hash.slice(1);
    if (!Object.hasOwn(routes, route)) {
      route = 'learn';
      history.replaceState(null, '', '#learn');
    }
    pages.forEach(page => { page.hidden = page.dataset.page !== route; });
    links.forEach(link => {
      if (link.dataset.route === route) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
    document.title = `${t(routes[route])} · ${t('大模型学习助手')}`;
    window.learnPage.setActive(route === 'learn');
    window.library.setActive(route === 'library');
    window.assessment.setActive(route === 'assessment');
    window.progressPage.setActive(route === 'progress');
    window.reportsPage.setActive(route === 'reports');
  }

  function showConnection(connected) {
    status.classList.toggle('is-connected', connected);
    statusLabel.textContent = connected ? t('后台已连接') : t('后台未连接');
    status.title = connected ? t('后台接口连接正常') : t('无法连接后台，稍后将自动重试');
  }

  async function refreshConnection() {
    if (checking) return;
    clearTimeout(checkTimer);
    if (document.hidden) return;
    checking = true;
    try {
      const connected = navigator.onLine && await window.api.checkConnection();
      showConnection(Boolean(connected && navigator.onLine));
    } finally {
      checking = false;
      checkTimer = setTimeout(refreshConnection, 15000);
    }
  }

  window.addEventListener('hashchange', renderRoute);
  window.addEventListener('online', refreshConnection);
  window.addEventListener('offline', () => showConnection(false));
  document.addEventListener('visibilitychange', refreshConnection);
  renderRoute();
  refreshConnection();
})();
