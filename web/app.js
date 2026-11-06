'use strict';

const $ = (id) => document.getElementById(id);

let currentCourses = [];
let allGradeCount = 0;
let availableTerms = [];
let currentTerm = '';
let currentTermLabel = '';
let latestOverview = null;
let formFilled = false;
let toastTimer = null;
let autoLoaded = false;

/* ---------- 基础工具 ---------- */

async function api(path, payload) {
  const options =
    payload === undefined
      ? {}
      : {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        };
  const resp = await fetch(path, options);
  let body = null;
  try {
    body = await resp.json();
  } catch {
    throw new Error(`服务返回异常（HTTP ${resp.status}）`);
  }
  if (!resp.ok || !body.ok) {
    throw new Error(body && body.error ? body.error : `请求失败（HTTP ${resp.status}）`);
  }
  return body.data;
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
  });
}

function fmtTime(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function toast(message, type) {
  const el = $('toast');
  el.textContent = message;
  el.className = 'toast show' + (type ? ' ' + type : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.className = 'toast';
  }, 3200);
}

/* ---------- 标签页 ---------- */

function switchTab(name) {
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.tab === name);
  });
  document.querySelectorAll('.panel').forEach((panel) => {
    panel.classList.toggle('active', panel.id === 'panel-' + name);
  });
}

/* ---------- 已出科目 ---------- */

function renderCourses() {
  const keyword = $('search').value.trim();
  const list = currentCourses.filter((name) => !keyword || name.includes(keyword));

  $('gradeTable').querySelector('tbody').innerHTML = list
    .map((name, index) => `<tr><td class="idx">${index + 1}</td><td class="course">${esc(name)}</td></tr>`)
    .join('');

  $('gradeCount').textContent = list.length ? `${list.length} 门` : '';

  const empty = $('gradeEmpty');
  if (list.length) {
    empty.style.display = 'none';
    return;
  }
  empty.style.display = 'block';
  if (currentCourses.length) {
    empty.textContent = '没有匹配的科目。';
  } else if (!latestOverview || !latestOverview.configured) {
    empty.textContent = '还没有配置账号。先去「账号与通知」填写学号和密码。';
  } else if (currentTerm && availableTerms.length && !availableTerms.includes(currentTerm)) {
    empty.textContent = `成绩数据里没有识别到「${currentTermLabel || currentTerm}」这个学期。`;
  } else {
    empty.textContent = `${currentTermLabel || '本学期'}还没有出成绩的科目。`;
  }
}

function renderStats(overview) {
  const state = (overview && overview.state) || {};
  const baseline = state.baseline_ready ? `${state.count} 条` : '未建立';
  const lastCheck = state.updated_at ? fmtTime(state.updated_at) : '—';

  $('stats').innerHTML = `
    <div class="stat"><div class="label">本学期已出</div><div class="value">${currentCourses.length || '—'}</div></div>
    <div class="stat"><div class="label">全部学期</div><div class="value">${allGradeCount || '—'}</div></div>
    <div class="stat"><div class="label">监控基线</div><div class="value small">${baseline}</div></div>
    <div class="stat"><div class="label">最近检查</div><div class="value small">${lastCheck}</div></div>
  `;
}

async function loadCourses(options) {
  const silent = !!(options && options.silent);
  const btn = $('btnTestLogin');

  if (!silent) {
    btn.disabled = true;
    btn.textContent = '读取中…';
    $('termTitle').textContent = '正在读取本学期出分情况…';
  }

  try {
    const data = await api('/api/test-login', {});
    currentCourses = data.courses || [];
    allGradeCount = data.all_count || 0;
    availableTerms = data.available_terms || [];
    currentTerm = data.term || '';
    currentTermLabel = data.term_label || '';

    $('termTitle').textContent = currentTermLabel ? `${currentTermLabel} 已出科目` : '本学期已出科目';
    $('termSub').textContent = `本学期已出 ${currentCourses.length} 门，全部学期共 ${allGradeCount} 门`;
    renderCourses();
    renderStats(latestOverview);
    if (!silent) toast(`读取完成，本学期已出 ${currentCourses.length} 门`, 'success');
  } catch (error) {
    $('termTitle').textContent = '本学期已出科目';
    $('termSub').textContent = silent ? '自动读取失败，请检查账号密码后手动重试。' : '';
    if (!silent) toast(error.message, 'error');
    renderCourses();
  } finally {
    if (!silent) {
      btn.disabled = false;
      btn.textContent = '读取本学期已出科目';
    }
  }
}

/* ---------- 运行记录 ---------- */

function renderRuns(runs) {
  $('runs').innerHTML = runs
    .map((run) => {
      const ok = !!run.ok;
      let summary;
      if (!ok) {
        summary = `<span class="run-error">${esc(run.error || '运行失败')}</span>`;
      } else if (run.baseline_created) {
        summary = `建立基线，共 ${run.total} 门`;
      } else if (run.new && run.new.length) {
        summary = `新增 ${run.new.length} 门，共 ${run.total} 门`;
      } else {
        summary = `无新增，共 ${run.total} 门`;
      }
      const news = ok && run.new && run.new.length ? `<div class="run-new">${run.new.map(esc).join('<br>')}</div>` : '';
      return `<li>
        <span class="run-dot ${ok ? 'ok' : 'bad'}"></span>
        <div class="run-main">
          <div>${summary}</div>
          <div class="run-meta">${fmtTime(run.at)} · ${run.source === 'scheduled' ? '定时' : '手动'} · 耗时 ${run.elapsed ?? '—'}s</div>
          ${news}
        </div>
      </li>`;
    })
    .join('');
  $('runsEmpty').style.display = runs.length ? 'none' : 'block';
}

/* ---------- 概览 ---------- */

function applyOverview(data, fillForm) {
  latestOverview = data;
  const configured = !!data.configured;
  $('statusText').textContent = configured ? `已配置 · ${data.username}` : '尚未配置';
  $('statusDot').className = 'dot ' + (configured ? (data.state.failures ? 'warn' : 'ok') : '');

  if (fillForm && !formFilled) {
    $('username').placeholder = '留空表示不修改';
    $('webhook').placeholder = data.has_webhook
      ? `当前 ${data.webhook}，留空不修改`
      : 'https://oapi.dingtalk.com/robot/send?access_token=...';
    $('secret').placeholder = data.has_secret ? '已保存，留空不修改' : 'SEC 开头；用关键词校验时留空';
    if (data.channel) $('channel').value = data.channel;
    formFilled = true;
  }

  const sched = data.scheduler || {};
  $('schedState').textContent = sched.running ? `运行中 · 每 ${sched.interval} 分钟` : '未启动';
  $('schedState').className = 'badge' + (sched.running ? ' on' : '');
  $('btnSchedToggle').textContent = sched.running ? '停止' : '启动';

  renderStats(data);
}

async function refresh() {
  const data = await api('/api/overview');
  applyOverview(data, false);
  const runsData = await api('/api/runs');
  renderRuns(runsData.runs || []);
}

/* ---------- 交互 ---------- */

function bind() {
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      switchTab(tab.dataset.tab);
      if (tab.dataset.tab === 'grades' && !currentCourses.length && latestOverview && latestOverview.configured) {
        loadCourses({ silent: true });
      }
    });
  });

  $('search').addEventListener('input', renderCourses);

  $('btnSave').addEventListener('click', async () => {
    const btn = $('btnSave');
    const payload = { channel: $('channel').value };
    const username = $('username').value.trim();
    const password = $('password').value;
    const webhook = $('webhook').value.trim();
    const secret = $('secret').value.trim();
    if (username) payload.username = username;
    if (password) payload.password = password;
    if (webhook) payload.webhook = webhook;
    if (secret) payload.secret = secret;

    btn.disabled = true;
    btn.textContent = '保存中…';
    try {
      await api('/api/config', payload);
      $('password').value = '';
      $('secret').value = '';
      formFilled = false;
      const data = await api('/api/overview');
      applyOverview(data, true);
      toast('配置已保存', 'success');
    } catch (error) {
      toast(error.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = '保存配置';
    }
  });

  $('btnTestLogin').addEventListener('click', async () => {
    switchTab('grades');
    await loadCourses();
  });

  $('btnTestNotify').addEventListener('click', async () => {
    const btn = $('btnTestNotify');
    btn.disabled = true;
    btn.textContent = '发送中…';
    try {
      await api('/api/test-notify', {});
      toast('测试消息已发送，请查看群消息', 'success');
    } catch (error) {
      toast(error.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = '发送测试消息';
    }
  });

  $('btnRunNow').addEventListener('click', async () => {
    const btn = $('btnRunNow');
    btn.disabled = true;
    btn.textContent = '检查中…';
    try {
      const entry = await api('/api/run', {});
      if (entry.baseline_created) {
        toast(`已建立基线，共 ${entry.total} 门，本次不推送`, 'success');
      } else if (entry.new && entry.new.length) {
        toast(`新增 ${entry.new.length} 门成绩，已推送`, 'success');
      } else {
        toast('检查完成，没有新增成绩', 'success');
      }
      await refresh();
    } catch (error) {
      toast(error.message, 'error');
      await refresh().catch(() => {});
    } finally {
      btn.disabled = false;
      btn.textContent = '立即检查一次';
    }
  });

  $('btnReset').addEventListener('click', async () => {
    if (!window.confirm('清空后下次运行会重新建立基线，期间不会提醒新增。确定清空？')) return;
    const btn = $('btnReset');
    btn.disabled = true;
    try {
      await api('/api/reset', {});
      await refresh();
      toast('已清空监控基线', 'success');
    } catch (error) {
      toast(error.message, 'error');
    } finally {
      btn.disabled = false;
    }
  });

  $('btnSchedToggle').addEventListener('click', async () => {
    const btn = $('btnSchedToggle');
    const running = $('schedState').classList.contains('on');
    btn.disabled = true;
    try {
      if (running) {
        await api('/api/scheduler/stop', {});
        toast('已停止本机监控', 'success');
      } else {
        await api('/api/scheduler/start', { interval: Number($('interval').value) });
        toast('已启动本机监控', 'success');
      }
      await refresh();
    } catch (error) {
      toast(error.message, 'error');
    } finally {
      btn.disabled = false;
    }
  });
}

/* ---------- 启动 ---------- */

(async function boot() {
  bind();
  try {
    const data = await api('/api/overview');
    applyOverview(data, true);
    const runsData = await api('/api/runs');
    renderRuns(runsData.runs || []);

    if (data.configured) {
      switchTab('grades');
      $('termSub').textContent = '正在读取…';
      if (!autoLoaded) {
        autoLoaded = true;
        loadCourses({ silent: true });
      }
    } else {
      switchTab('config');
      renderCourses();
    }
  } catch (error) {
    toast('初始化失败：' + error.message, 'error');
  }

  setInterval(() => {
    refresh().catch(() => {});
  }, 15000);
})();
