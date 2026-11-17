/* PenguinTerm — application controller.
 * Auth, tabs, sidebar, status bar, transfers, diagnostics, settings, rooms.
 */
'use strict';

/* ─────────────────────────── state ─────────────────────────── */
const state = {
  user: null,
  system: null,
  tabs: new Map(),       // sessionId -> TerminalTab
  activeTab: null,
  activePanel: 'panel-sessions',
  servers: [],
  rooms: [],
  users: [],
  transfers: [],
  settings: null,
  online: new Map(),     // username -> connection count
  fontSize: 13,
  cursorStyle: 'block',
  cursorBlink: true,
  scrollback: 5000,
  compatEnabled: false,
  pendingSendPath: null,
  reconnectDelays: {},
};

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const osBadge = (platform) =>
  platform === 'win32'
    ? '<span class="os-badge os-windows">WINDOWS</span>'
    : '<span class="os-badge os-linux">LINUX</span>';
const fmtBytes = (n) => {
  if (!n && n !== 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0; let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
};
const timeAgo = (ts) => {
  if (!ts) return '';
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};

function toast(message, kind) {
  const el = document.createElement('div');
  el.className = `toast ${kind || ''}`;
  el.textContent = message;
  $('#toasts').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateX(20px)'; }, 4200);
  setTimeout(() => el.remove(), 4800);
}

/* ─────────────────────────── auth flow ─────────────────────────── */
function showAuthScreen(mode) {
  $('#app').classList.add('hidden');
  $('#auth-screen').classList.remove('hidden');
  $('#auth-setup-panel').classList.toggle('hidden', mode !== 'setup');
  $('#auth-login-panel').classList.toggle('hidden', mode !== 'login');
  $('#auth-invite-panel').classList.toggle('hidden', mode !== 'invite');
  $('#auth-toggle-invite').classList.toggle('hidden', mode === 'invite');
  hideAuthError();
}
function showAuthError(message) {
  const el = $('#auth-error');
  el.textContent = message;
  el.classList.remove('hidden');
}
function hideAuthError() { $('#auth-error').classList.add('hidden'); }

function getInviteToken() {
  const params = new URLSearchParams(location.search);
  return params.get('invite') || new URLSearchParams((location.hash || '').replace(/^#/, '')).get('invite');
}

async function bootstrap() {
  const invite = getInviteToken();
  let status;
  try { status = await api.get('/api/auth/status'); }
  catch (_) { document.body.innerHTML = '<div style="padding:40px;font-family:monospace">PenguinTerm server is unreachable.</div>'; return; }

  if (invite) {
    showAuthScreen('invite');
    $('#invite-form').dataset.token = invite;
    return;
  }
  if (status.setup_required) { showAuthScreen('setup'); return; }
  try {
    const me = await api.get('/api/auth/me', { allow401: true });
    state.user = me;
    await enterApp();
  } catch (_) {
    showAuthScreen('login');
  }
}

function wireAuthForms() {
  $('#setup-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    hideAuthError();
    const form = event.target;
    if (form.password.value !== form.confirm.value) { showAuthError('Passwords do not match'); return; }
    try {
      await api.post('/api/auth/setup', { username: form.username.value, password: form.password.value });
      const me = await api.get('/api/auth/me');
      state.user = me;
      await enterApp();
      toast('Owner account created. Welcome to PenguinTerm!', 'success');
    } catch (err) { showAuthError(err.detail || err.message); }
  });

  $('#login-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    hideAuthError();
    const form = event.target;
    try {
      await api.post('/api/auth/login', { username: form.username.value, password: form.password.value }, { allow401: true });
      const me = await api.get('/api/auth/me');
      state.user = me;
      await enterApp();
    } catch (err) { showAuthError(err.detail || 'Invalid username or password'); }
  });

  $('#invite-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    hideAuthError();
    const form = event.target;
    try {
      await api.post('/api/auth/register-with-invite', {
        invite_token: form.dataset.token,
        username: form.username.value,
        password: form.password.value,
      });
      const me = await api.get('/api/auth/me');
      state.user = me;
      history.replaceState(null, '', '/'); // strip the token from the URL
      await enterApp();
      toast('Account created — you are connected to this server.', 'success');
    } catch (err) { showAuthError(err.detail || err.message); }
  });

  $('#auth-toggle-invite').addEventListener('click', () => {
    const token = prompt('Paste your invitation link or token:');
    if (!token) return;
    let extracted = token.trim();
    try {
      const url = new URL(extracted);
      extracted = url.searchParams.get('invite') || extracted;
    } catch (_) { /* raw token */ }
    $('#auth-invite-panel').classList.remove('hidden');
    $('#auth-login-panel').classList.add('hidden');
    $('#auth-setup-panel').classList.add('hidden');
    $('#invite-form').dataset.token = extracted;
  });

  window.addEventListener('pt:unauthorized', () => { state.user = null; showAuthScreen('login'); });
  $('#btn-logout').addEventListener('click', async () => {
    try { await api.post('/api/auth/logout'); } catch (_) {}
    state.user = null;
    for (const tab of state.tabs.values()) tab.dispose();
    state.tabs.clear();
    showAuthScreen('login');
  });
}

/* ─────────────────────────── app entry ─────────────────────────── */
async function enterApp() {
  $('#auth-screen').classList.add('hidden');
  $('#app').classList.remove('hidden');
  document.body.classList.toggle('is-owner', state.user.role === 'owner');
  if (state.user.role !== 'owner') {
    $$('.owner-only').forEach((el) => { el.style.display = 'none'; });
  }

  state.system = await api.get('/api/system');
  $('#self-server-name').textContent = state.system.hostname;
  $('#user-chip').innerHTML = `<b>${esc(state.user.username)}</b> · ${esc(state.user.role)}`;
  $('#self-server-dot').className = 'dot dot-green';

  try { state.settings = await api.get('/api/settings'); }
  catch (_) { state.settings = { settings: {} }; }
  state.compatEnabled = !!(state.settings.settings && state.settings.settings.compat_layer);

  loadClientPrefs();
  connectEvents();
  await Promise.all([refreshSessions(), refreshServers(), refreshRooms(), refreshUsers(), refreshTransfers()]);
  if (state.tabs.size === 0) await openNewTab(); // first-run nicety
  setActivePanel(state.activePanel);
}

function loadClientPrefs() {
  const get = (k, fallback) => {
    try { const v = localStorage.getItem(k); return v === null ? fallback : JSON.parse(v); }
    catch { return fallback; }
  };
  state.fontSize = get('pt_font_size', 13);
  state.cursorStyle = get('pt_cursor', 'block');
  state.cursorBlink = get('pt_cursor_blink', true);
  state.scrollback = get('pt_scrollback', 5000);
  $('#setting-font-size').value = state.fontSize;
  $('#setting-cursor').value = state.cursorStyle;
  $('#setting-cursor-blink').checked = state.cursorBlink;
  $('#setting-scrollback').value = state.scrollback;
}
function saveClientPrefs() {
  localStorage.setItem('pt_font_size', JSON.stringify(state.fontSize));
  localStorage.setItem('pt_cursor', JSON.stringify(state.cursorStyle));
  localStorage.setItem('pt_cursor_blink', JSON.stringify(state.cursorBlink));
  localStorage.setItem('pt_scrollback', JSON.stringify(state.scrollback));
}

/* ─────────────────────────── events channel ─────────────────────────── */
function connectEvents() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${protocol}//${location.host}/ws/events`);
  state.eventsWs = ws;
  ws.onmessage = (event) => {
    let msg; try { msg = JSON.parse(event.data); } catch { return; }
    switch (msg.event) {
      case 'hello':
        bumpOnline(msg.payload.username, +1);
        break;
      case 'user_online': bumpOnline(msg.payload.username, +1); break;
      case 'user_offline': bumpOnline(msg.payload.username, -1); break;
      case 'session_created':
      case 'session_exited':
      case 'session_closed':
        refreshSessions();
        updateEmptyState();
        break;
      case 'transfer_update':
        refreshTransfers();
        if (msg.payload && msg.payload.status === 'done') toast(`Transfer complete: ${msg.payload.filename}`, 'success');
        break;
      case 'viewer_dropped':
        toast('A viewer was disconnected (slow connection)', 'warn');
        break;
    }
  };
  ws.onclose = () => { setTimeout(connectEvents, 3000); };
}

function bumpOnline(username, delta) {
  if (!username) return;
  const count = (state.online.get(username) || 0) + delta;
  if (count <= 0) state.online.delete(username); else state.online.set(username, count);
  renderOnlineUsers();
}

function renderOnlineUsers() {
  const el = $('#online-users');
  const names = Array.from(state.online.keys()).slice(0, 6);
  if (!names.length) { el.innerHTML = ''; return; }
  el.innerHTML = names.map((name) => {
    const hue = [...name].reduce((acc, ch) => acc + ch.charCodeAt(0), 0) % 360;
    const initials = name.slice(0, 2).toUpperCase();
    return `<span class="online-pill" style="background:hsl(${hue},45%,72%)" title="${esc(name)} — connected">${esc(initials)}</span>`;
  }).join('');
}

/* ─────────────────────────── sidebar panels ─────────────────────────── */
function setActivePanel(id) {
  state.activePanel = id;
  $$('.side-tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.panel === id));
  $$('.side-panel').forEach((panel) => panel.classList.toggle('active', panel.id === id));
}

function wireSidebar() {
  $$('.side-tab').forEach((tab) => tab.addEventListener('click', () => setActivePanel(tab.dataset.panel)));
  $('#btn-new-tab-side').addEventListener('click', () => openNewTab());
}

/* sessions ---------------------------------------------------------- */
async function refreshSessions() {
  try {
    const data = await api.get('/api/sessions');
    state.sessionList = data.sessions || [];
    renderSessionList();
    renderEmptyShared();
  } catch (_) { /* transient */ }
}

function renderSessionList() {
  const el = $('#session-list');
  const sessions = state.sessionList || [];
  if (!sessions.length) {
    el.innerHTML = '<p class="side-hint">No sessions yet. Open one with “+ New terminal”.</p>';
    return;
  }
  el.innerHTML = sessions.map((s) => {
    const isShared = s.kind === 'shared';
    const targetHost = state.system ? state.system.platform : 'linux';
    const viewers = s.viewer_count ? ` · ${s.viewer_count} watching` : '';
    const exited = s.exited ? ' · exited' : '';
    return `
      <div class="list-item clickable" data-session="${esc(s.id)}">
        <div class="li-row">
          ${osBadge(targetHost)}
          <span class="li-title" title="${esc(s.title)}">${esc(s.title)}</span>
          <span class="pill ${isShared ? 'pill-shared' : 'pill-local'}">${isShared ? 'SHARED' : 'LOCAL'}</span>
        </div>
        <div class="li-sub">
          <span>${esc(s.shell_name)}</span><span>by ${esc(s.created_by)}</span>
          <span>${esc(viewers + exited)}</span>
        </div>
        <div class="li-actions">
          ${isShared && state.user.role === 'owner' ? `<button class="mini-btn" data-action="mode" data-id="${esc(s.id)}">${s.input_mode === 'all' ? 'Lock input' : 'Unlock input'}</button>` : ''}
          <button class="mini-btn" data-action="duplicate" data-id="${esc(s.id)}">Duplicate</button>
          <button class="mini-btn danger" data-action="close" data-id="${esc(s.id)}">Close</button>
        </div>
      </div>`;
  }).join('');

  $$('[data-session]', el).forEach((item) => {
    item.addEventListener('click', (event) => {
      if (event.target.closest('[data-action]')) return;
      attachToSession(item.dataset.session);
    });
  });
  $$('[data-action]', el).forEach((button) => {
    button.addEventListener('click', () => sessionAction(button.dataset.action, button.dataset.id));
  });
}

async function sessionAction(action, id) {
  const session = (state.sessionList || []).find((s) => s.id === id);
  try {
    if (action === 'close') {
      if (!confirm(`Close session "${session ? session.title : id}"? The shell process will be terminated.`)) return;
      await api.del(`/api/sessions/${encodeURIComponent(id)}`);
      closeTabLocal(id);
      toast('Session closed');
    } else if (action === 'duplicate') {
      const created = await api.post(`/api/sessions/${encodeURIComponent(id)}/duplicate`);
      await refreshSessions();
      await attachToSession(created.id);
    } else if (action === 'mode') {
      const mode = session && session.input_mode === 'all' ? 'owner' : 'all';
      await api.patch(`/api/sessions/${encodeURIComponent(id)}`, { input_mode: mode });
      toast(mode === 'owner' ? 'Input locked to the owner' : 'Editors can type again', 'success');
      await refreshSessions();
    }
  } catch (err) { toast(err.detail || err.message, 'error'); }
}

async function attachToSession(sessionId) {
  if (state.tabs.has(sessionId)) { activateTab(sessionId); return; }
  let meta = (state.sessionList || []).find((s) => s.id === sessionId);
  if (!meta) {
    try { meta = (await api.get('/api/sessions')).sessions.find((s) => s.id === sessionId); } catch (_) {}
  }
  if (!meta) { toast('Session no longer exists', 'error'); return; }
  createTabForSession(meta);
}

/* servers ---------------------------------------------------------- */
async function refreshServers() {
  try {
    const data = await api.get('/api/servers');
    state.servers = data.servers || [];
    renderServers();
  } catch (_) {}
}

function renderServers() {
  const el = $('#server-list');
  if (!state.servers.length) {
    el.innerHTML = '<p class="side-hint">No servers yet. Add one with “+ Add” — it just checks reachability and opens the friend’s UI.</p>';
    return;
  }
  el.innerHTML = state.servers.map((s) => `
    <div class="list-item">
      <div class="li-row">
        <span class="dot ${s.last_status === 'online' ? 'dot-green' : s.last_status === 'offline' ? 'dot-red' : 'dot-gray'}"></span>
        <span class="li-title">${esc(s.label)}</span>
        <span class="li-sub">${esc(s.host)}:${s.port}</span>
      </div>
      <div class="li-sub"><span>last status: ${esc(s.last_status || 'unknown')}</span>${s.username ? `<span>as ${esc(s.username)}</span>` : ''}</div>
      <div class="li-actions">
        <button class="mini-btn" data-server-action="open" data-id="${s.id}">Open UI</button>
        <button class="mini-btn" data-server-action="test" data-id="${s.id}">Test</button>
        <button class="mini-btn danger" data-server-action="remove" data-id="${s.id}">Remove</button>
      </div>
    </div>`).join('');

  $$('[data-server-action]', el).forEach((button) => {
    button.addEventListener('click', () => serverAction(button.dataset.serverAction, +button.dataset.id));
  });
}

async function serverAction(action, id) {
  const server = state.servers.find((s) => s.id === id);
  if (!server) return;
  const scheme = server.tls ? 'https' : 'http';
  if (action === 'open') {
    window.open(`${scheme}://${server.host}:${server.port}/`, '_blank', 'noopener');
  } else if (action === 'test') {
    toast('Probing…');
    try {
      const result = await api.post(`/api/servers/${id}/test`);
      toast(result.reachable
        ? `Reachable — ${result.latency_ms}ms · PenguinTerm ${result.version || ''} (${result.platform || ''})`
        : `Unreachable — ${result.detail || 'no response'}`,
        result.reachable ? 'success' : 'error');
      await refreshServers();
    } catch (err) { toast(err.detail || err.message, 'error'); }
  } else if (action === 'remove') {
    if (!confirm(`Remove server "${server.label}"?`)) return;
    await api.del(`/api/servers/${id}`);
    await refreshServers();
  }
}

/* rooms ----------------------------------------------------------- */
async function refreshRooms() {
  try {
    const data = await api.get('/api/rooms');
    state.rooms = data.rooms || [];
    renderRooms();
  } catch (_) {}
}

function renderRooms() {
  const el = $('#room-list');
  if (!state.rooms.length) {
    el.innerHTML = '<p class="side-hint">No rooms yet. Rooms group collaborators on this server — share the join code with friends who have accounts here.</p>';
    return;
  }
  el.innerHTML = state.rooms.map((r) => `
    <div class="list-item">
      <div class="li-row"><span class="li-title">${esc(r.name)}</span></div>
      <div class="li-sub"><span class="pill">${esc(r.code)}</span><button class="mini-btn" data-copy-room="${esc(r.code)}">Copy code</button></div>
    </div>`).join('');
  $$('[data-copy-room]', el).forEach((b) => b.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(b.dataset.copyRoom); toast('Room code copied', 'success'); }
    catch { toast('Clipboard unavailable', 'warn'); }
  }));
}

/* users & invites --------------------------------------------------- */
async function refreshUsers() {
  state.users = [state.user];
  if (state.user.role === 'owner') {
    try { state.users = (await api.get('/api/users')).users || []; } catch (_) {}
    try { state.invites = (await api.get('/api/invites')).invites || []; } catch (_) {}
  }
  renderUsers();
}

function renderUsers() {
  const el = $('#user-list');
  const users = state.users || [];
  el.innerHTML = users.map((u) => `
    <div class="list-item">
      <div class="li-row">
        <span class="li-title">${esc(u.username)}</span>
        <span class="pill pill-role-${esc(u.role)}">${esc(u.role.toUpperCase())}</span>
      </div>
      <div class="li-sub"><span>${esc(u.status)}</span>${u.last_seen ? `<span>last seen ${timeAgo(u.last_seen)}</span>` : ''}</div>
      ${state.user.role === 'owner' && u.role !== 'owner' ? `
        <div class="li-actions">
          <button class="mini-btn" data-user-action="toggle" data-id="${u.id}" data-status="${esc(u.status)}">${u.status === 'active' ? 'Block' : 'Activate'}</button>
          <button class="mini-btn danger" data-user-action="delete" data-id="${u.id}" data-name="${esc(u.username)}">Delete</button>
        </div>` : ''}
    </div>`).join('');

  $$('[data-user-action]', el).forEach((button) => {
    button.addEventListener('click', async () => {
      const id = +button.dataset.id;
      try {
        if (button.dataset.userAction === 'toggle') {
          const status = button.dataset.status === 'active' ? 'blocked' : 'active';
          await api.patch(`/api/users/${id}`, { status });
        } else if (button.dataset.userAction === 'delete') {
          if (!confirm(`Delete user "${button.dataset.name}"?`)) return;
          await api.del(`/api/users/${id}`);
        }
        await refreshUsers();
      } catch (err) { toast(err.detail || err.message, 'error'); }
    });
  });

  const inviteEl = $('#invite-list');
  const invites = (state.invites || []).filter((i) => (!i.expires_at || i.expires_at > Date.now() / 1000));
  if (state.user.role === 'owner' && invites.length) {
    inviteEl.innerHTML = '<p class="side-hint">Active invitations:</p>' + invites.map((i) => `
      <div class="list-item">
        <div class="li-sub"><span class="pill pill-role-${esc(i.role)}">${esc(i.role.toUpperCase())}</span>
          <span>${i.uses}/${i.max_uses} used</span>
          ${i.expires_at ? `<span>expires ${new Date(i.expires_at * 1000).toLocaleString()}</span>` : '<span>no expiry</span>'}
        </div>
        <div class="li-actions"><button class="mini-btn danger" data-invite-delete="${esc(i.token)}">Revoke</button></div>
      </div>`).join('');
    $$('[data-invite-delete]', inviteEl).forEach((b) => b.addEventListener('click', async () => {
      await api.del(`/api/invites/${encodeURIComponent(b.dataset.inviteDelete)}`);
      await refreshUsers();
    }));
  } else {
    inviteEl.innerHTML = '';
  }
}

/* transfers --------------------------------------------------------- */
async function refreshTransfers() {
  try {
    const data = await api.get('/api/transfers');
    state.transfers = data.transfers || [];
    renderTransfers();
  } catch (_) {}
}

function renderTransfers() {
  const el = $('#transfer-list');
  const items = state.transfers;
  if (!items.length) {
    el.innerHTML = '<p class="side-hint">No transfers yet. Files you or your friends send appear here with SHA-256 verification.</p>';
    return;
  }
  el.innerHTML = items.slice(0, 30).map((t) => `
    <div class="list-item transfer-item">
      <div style="min-width:0;flex:1">
        <div class="li-row"><span class="li-title" title="${esc(t.filename)}">${esc(t.filename)}</span>
          <span class="pill">${esc(t.direction || 'receive')}</span></div>
        <div class="t-meta">${fmtBytes(t.size)} · ${esc(t.status)} · ${timeAgo(t.created_at)}${t.peer ? ` · ${esc(t.peer)}` : ''}</div>
        ${t.sha256 ? `<div class="t-meta" title="SHA-256">${esc(t.sha256.slice(0, 16))}…</div>` : ''}
      </div>
      <div class="li-actions" style="flex-direction:column;gap:4px">
        ${t.id ? `<button class="mini-btn" data-transfer="download" data-id="${t.id}" title="Download">⬇</button>` : ''}
        ${t.path ? `<button class="mini-btn" data-transfer="peer" data-path="${esc(t.path)}" title="Send to a server">➤</button>` : ''}
        <button class="mini-btn danger" data-transfer="delete" data-id="${t.id}" title="Delete">✕</button>
      </div>
    </div>`).join('');

  $$('[data-transfer]', el).forEach((button) => {
    button.addEventListener('click', () => transferAction(button));
  });
}

function transferAction(button) {
  const action = button.dataset.transfer;
  if (action === 'download') {
    window.open(`/api/transfer/download?transfer_id=${encodeURIComponent(button.dataset.id)}`, '_blank');
  } else if (action === 'peer') {
    openSendToPeer(button.dataset.path);
  } else if (action === 'delete') {
    if (!confirm('Delete this transfer and its stored file?')) return;
    api.del(`/api/transfers/${button.dataset.id}`).then(refreshTransfers).catch((e) => toast(e.detail, 'error'));
  }
}

async function handleUpload(files) {
  for (const file of files) {
    const bar = document.createElement('div');
    bar.className = 'list-item';
    bar.innerHTML = `<div class="li-row"><span class="li-title">${esc(file.name)}</span><span class="t-meta">${fmtBytes(file.size)}</span></div>
      <div class="progress-track"><div class="progress-fill" style="width:0%"></div></div>`;
    $('#transfer-progress').appendChild(bar);
    const fill = $('.progress-fill', bar);
    try {
      await api.upload('/api/transfer/upload', file, (frac) => { fill.style.width = `${Math.round(frac * 100)}%`; });
      fill.style.width = '100%';
      toast(`Uploaded ${file.name}`, 'success');
    } catch (err) {
      bar.remove();
      toast(`Upload failed: ${err.detail || err.message}`, 'error');
      continue;
    }
    setTimeout(() => bar.remove(), 1200);
  }
  await refreshTransfers();
}

function openSendToPeer(path) {
  state.pendingSendPath = path;
  const select = $('#send-peer-server');
  select.innerHTML = state.servers.map((s) =>
    `<option value="${s.id}">${esc(s.label)} — ${esc(s.host)}:${s.port}</option>`).join('')
    || '<option value="">No saved servers — add one first</option>';
  $('#send-peer-form').username.value = '';
  $('#send-peer-form').password.value = '';
  openModal('modal-send-peer');
}


/* ─────────────────────────── tabs ─────────────────────────── */
function createTabForSession(session) {
  const host = $('#terminal-host');
  const tab = new TerminalTab(session, {
    fontSize: state.fontSize,
    cursorStyle: state.cursorStyle,
    cursorBlink: state.cursorBlink,
    scrollback: state.scrollback,
    compatEnabled: state.compatEnabled,
    onStatus: (status) => { updateTabProcessState(session.id, status); updateStatusBar(); },
    onMeta: () => { updateStatusBar(); },
    onLatency: (ms) => { const el = $('#status-latency'); if (state.activeTab === session.id) el.textContent = `${ms} ms`; },
    onViewers: () => { updateStatusBar(); refreshSessions(); },
    onExit: () => { updateStatusBar(); },
    onTitle: (title) => {
      const existing = state.tabs.get(session.id);
      if (existing && title && title !== existing.session.title) {
        api.patch(`/api/sessions/${encodeURIComponent(session.id)}`, { title }).catch(() => {});
        existing.session.title = title;
        renderTabBar();
      }
    },
  });
  tab.mount(host);
  state.tabs.set(session.id, tab);
  renderTabBar();
  activateTab(session.id);
  updateEmptyState();
}

async function openNewTab(shellId, kind, title) {
  const btn = $('#btn-new-tab');
  btn.disabled = true;
  try {
    const meta = await api.post('/api/sessions', {
      shell_id: shellId || (state.system && state.system.default_shell) || undefined,
      kind: kind || 'local',
      title: title || undefined,
      cols: 120, rows: 30,
    });
    state.sessionList = (state.sessionList || []).concat([meta]);
    renderSessionList();
    createTabForSession(meta);
  } catch (err) {
    toast(err.detail || err.message, 'error');
  } finally {
    btn.disabled = false;
  }
}

async function openSharedTab() {
  if (state.user.role !== 'owner') { toast('Only the owner can open shared sessions', 'warn'); return; }
  const name = prompt('Name for the shared session (everyone on this server can join):', 'shared');
  if (name === null) return;
  await openNewTab(state.system && state.system.default_shell, 'shared', name.trim() || 'shared');
}

function activateTab(sessionId) {
  state.activeTab = sessionId;
  for (const [id, tab] of state.tabs) {
    if (id === sessionId) tab.show(); else tab.hide();
  }
  renderTabBar();
  updateStatusBar();
}

function closeTabLocal(sessionId) {
  const tab = state.tabs.get(sessionId);
  if (tab) { tab.dispose(); state.tabs.delete(sessionId); }
  if (state.activeTab === sessionId) {
    const next = state.tabs.keys().next().value || null;
    state.activeTab = next;
    if (next) activateTab(next); else updateStatusBar();
  }
  renderTabBar();
  updateEmptyState();
}

async function closeTab(sessionId) {
  try { await api.del(`/api/sessions/${encodeURIComponent(sessionId)}`); } catch (_) {}
  closeTabLocal(sessionId);
}

function renderTabBar() {
  const el = $('#tabs');
  el.innerHTML = [...state.tabs.values()].map((tab) => {
    const s = tab.session;
    const kind = s.kind === 'shared' ? '<span class="tab-kind shared">SHARED</span>' : '<span class="tab-kind local">LOCAL</span>';
    const active = state.activeTab === s.id ? ' active' : '';
    return `
      <div class="tab${active}" data-tab="${esc(s.id)}" title="${esc(s.shell_name)} · ${esc(s.title)}">
        ${kind}<span class="tab-title">${esc(s.title || s.shell_name)}</span>
        <button class="tab-close" data-close="${esc(s.id)}" title="Close (Ctrl+Shift+W)">×</button>
      </div>`;
  }).join('');
  $$('[data-tab]', el).forEach((tabEl) => {
    tabEl.addEventListener('click', () => activateTab(tabEl.dataset.tab));
    tabEl.addEventListener('dblclick', async () => {
      const id = tabEl.dataset.tab;
      const tab = state.tabs.get(id);
      const title = prompt('Rename session:', tab ? tab.session.title : '');
      if (title === null) return;
      try {
        await api.patch(`/api/sessions/${encodeURIComponent(id)}`, { title: title.trim() || 'terminal' });
        state.tabs.get(id).session.title = title.trim() || 'terminal';
        renderTabBar();
      } catch (err) { toast(err.detail || err.message, 'error'); }
    });
  });
  $$('[data-close]', el).forEach((btn) => btn.addEventListener('click', (event) => {
    event.stopPropagation();
    closeTab(btn.dataset.close);
  }));
}

function updateTabProcessState(sessionId, status) {
  const el = $(`#tabs [data-tab="${CSS.escape(sessionId)}"]`);
  if (el) el.classList.toggle('disconnected', status !== 'connected');
}

function cycleTab(direction) {
  const ids = [...state.tabs.keys()];
  if (ids.length < 2) return;
  const idx = ids.indexOf(state.activeTab);
  const next = ids[(idx + direction + ids.length) % ids.length];
  activateTab(next);
}

function updateEmptyState() {
  const hasTabs = state.tabs.size > 0;
  $('#empty-state').classList.toggle('hidden', hasTabs);
  $('#terminal-host').classList.toggle('hidden', !hasTabs);
}

function renderEmptyShared() {
  const el = $('#empty-shared');
  const shared = (state.sessionList || []).filter((s) => s.kind === 'shared' && !s.exited);
  if (!shared.length) { el.innerHTML = ''; return; }
  el.innerHTML = '<p class="muted">Join a shared session:</p>' + shared.map((s) => `
    <button class="btn btn-ghost" data-join="${esc(s.id)}">${esc(s.title)} — ${esc(s.shell_name)} (by ${esc(s.created_by)})</button>
  `).join('');
  $$('[data-join]', el).forEach((b) => b.addEventListener('click', () => attachToSession(b.dataset.join)));
}

/* ─────────────────────────── status bar ─────────────────────────── */
function updateStatusBar() {
  const tab = state.activeTab ? state.tabs.get(state.activeTab) : null;
  const kindEl = $('#status-kind'), osEl = $('#status-os'), shellEl = $('#status-shell');
  const restrictedEl = $('#status-restricted'), viewersEl = $('#status-viewers'), processEl = $('#status-process');
  if (!tab) {
    kindEl.textContent = '—'; kindEl.className = 'status-item status-kind';
    osEl.textContent = '—'; shellEl.textContent = '—';
    restrictedEl.classList.add('hidden'); viewersEl.textContent = ''; processEl.textContent = '—';
    $('#status-latency').textContent = '—';
    return;
  }
  const s = tab.session;
  const isShared = s.kind === 'shared';
  kindEl.textContent = isShared ? 'SHARED' : 'LOCAL';
  kindEl.className = `status-item status-kind ${isShared ? 'shared' : 'local'}`;
  osEl.innerHTML = s.host_os === 'win32' ? '⊞ Windows' : '🐧 Linux';
  shellEl.textContent = s.shell_name || '—';
  restrictedEl.classList.toggle('hidden', !s.restricted);
  viewersEl.textContent = isShared ? `${(s.viewers || []).length || s.viewer_count || 0} viewer(s)` : '';
  processEl.textContent = tab.exited ? 'process: exited' : 'process: running';
}

/* ─────────────────────────── modals ─────────────────────────── */
function openModal(id) {
  $('#modal-backdrop').classList.remove('hidden');
  $$('.modal').forEach((m) => m.classList.add('hidden'));
  $(`#${id}`).classList.remove('hidden');
}
function closeModals() {
  $('#modal-backdrop').classList.add('hidden');
  $$('.modal').forEach((m) => m.classList.add('hidden'));
}
function wireModals() {
  $('#modal-backdrop').addEventListener('click', (event) => { if (event.target.id === 'modal-backdrop') closeModals(); });
  $$('.modal-close').forEach((b) => b.addEventListener('click', closeModals));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !$('#modal-backdrop').classList.contains('hidden')) {
      if ($('#searchbar').classList.contains('hidden')) closeModals();
    }
  });
}

/* diagnostics */
async function showDiagnostics() {
  openModal('modal-diagnostics');
  $('#diagnostics-content').innerHTML = 'Loading…';
  try {
    const d = await api.get('/api/diagnostics');
    const listenClass = d.local_listen_ok ? 'diag-ok' : 'diag-bad';
    const cgnat = d.cgnat_likely ? '<span class="diag-warn">YES — inbound forwarding will not work</span>' : d.public_ip_known ? '<span class="diag-ok">not detected</span>' : '<span class="diag-warn">unknown</span>';
    const pub = d.public_ip_known ? esc(d.public_ip) : '<span class="diag-warn">unknown (lookup failed)</span>';
    $('#diagnostics-content').innerHTML = `
      <div class="diag-row"><span class="diag-label">Local IPs</span><span class="diag-value">${d.local_ips.map(esc).join('<br/>') || '—'}</span></div>
      <div class="diag-row"><span class="diag-label">Public IP</span><span class="diag-value">${pub}</span></div>
      <div class="diag-row"><span class="diag-label">Listening on</span><span class="diag-value">${esc(d.host)}:${d.port} (TLS=${d.tls ? 'yes' : 'no'})</span></div>
      <div class="diag-row"><span class="diag-label">Local listen check</span><span class="diag-value ${listenClass}">${d.local_listen_ok ? 'port is listening (local only — does NOT prove external reachability)' : 'port is NOT listening'}</span></div>
      <div class="diag-row"><span class="diag-label">Active connections</span><span class="diag-value">${d.active_connections}</span></div>
      <div class="diag-row"><span class="diag-label">Firewall</span><span class="diag-value">${esc(d.firewall)}</span></div>
      <div class="diag-row"><span class="diag-label">Tailscale</span><span class="diag-value">${d.mesh_vpn.tailscale ? `yes (${(d.mesh_vpn.tailscale_ips || []).map(esc).join(', ') || 'detected'})` : 'not detected'}</span></div>
      <div class="diag-row"><span class="diag-label">ZeroTier</span><span class="diag-value">${d.mesh_vpn.zerotier ? 'yes' : 'not detected'}</span></div>
      <div class="diag-row"><span class="diag-label">CGNAT</span><span class="diag-value">${cgnat}</span></div>
      <div class="diag-row"><span class="diag-label">External forwarding test</span><span class="diag-value diag-warn">${esc(d.external_test)}</span></div>
      <div class="diag-notes"><b>Notes</b><ul>${d.notes.map((n) => `<li>${esc(n)}</li>`).join('')}</ul></div>
      <p class="muted">Diagnostics ran in ${d.diagnostics_duration_ms} ms on ${esc(d.platform)}</p>`;
  } catch (err) {
    $('#diagnostics-content').innerHTML = `<div class="form-error">${esc(err.detail || err.message)}</div>`;
  }
}

/* server settings */
const CONFIG_FIELDS = [
  { key: 'host', label: 'Bind host', type: 'text' },
  { key: 'port', label: 'Port', type: 'number' },
  { key: 'tls', label: 'HTTPS/WSS (TLS)', type: 'checkbox', restart: true },
  { key: 'restricted_mode', label: 'Restricted mode for remote users', type: 'checkbox' },
  { key: 'allow_owner_full_shell', label: 'Full shell for local owner (unrestricted)', type: 'checkbox' },
  { key: 'remote_create_sessions', label: 'Editors may create sessions on this host', type: 'checkbox' },
  { key: 'remote_cwd_jail', label: 'Remote cwd jail', type: 'select', options: [['home', "Server owner's home"], ['none', 'No jail (not recommended)'], ['path', 'Custom path']] },
  { key: 'compat_layer', label: 'Compat layer (Linux↔Windows muscle memory)', type: 'checkbox' },
  { key: 'max_upload_bytes', label: 'Max upload bytes', type: 'number' },
  { key: 'max_sessions', label: 'Max sessions', type: 'number' },
  { key: 'scrollback_lines', label: 'Scrollback lines', type: 'number' },
  { key: 'session_idle_minutes', label: 'Reap idle sessions after (minutes, 0=never)', type: 'number' },
  { key: 'relay_url', label: 'Optional relay URL (wss://…)', type: 'text' },
  { key: 'public_ip_service', label: 'Public IP lookup service', type: 'text' },
];

async function showServerSettings() {
  if (state.user.role !== 'owner') { toast('Only the owner can change server settings', 'warn'); return; }
  openModal('modal-server-settings');
  await Promise.all([refreshServerStatus(), refreshSharedDirs(), refreshServerLogs(), refreshServerConfigForm()]);
}

async function refreshServerStatus() {
  try {
    const s = await api.get('/api/server/status');
    $('#server-status-content').innerHTML = `
      <div class="diag-row"><span class="diag-label">Status</span><span class="diag-value diag-ok">${esc(s.status)}</span></div>
      <div class="diag-row"><span class="diag-label">Version</span><span class="diag-value">${esc(s.version)}</span></div>
      <div class="diag-row"><span class="diag-label">Uptime</span><span class="diag-value">${s.uptime_seconds}s</span></div>
      <div class="diag-row"><span class="diag-label">Platform</span><span class="diag-value">${esc(s.platform)}</span></div>
      <div class="diag-row"><span class="diag-label">Bind</span><span class="diag-value">${esc(s.host)}:${s.port} (TLS=${s.tls ? 'yes' : 'no'})</span></div>
      <div class="diag-row"><span class="diag-label">Active sessions</span><span class="diag-value">${s.active_sessions} / ${s.total_sessions} sessions · ${s.active_ws_connections} WS connections</span></div>
      <div class="diag-row"><span class="diag-label">CPU / memory / threads</span><span class="diag-value">${s.resource.cpu_percent ?? '—'}% / ${s.resource.memory_mb ?? '—'} MB / ${s.resource.threads ?? '—'}</span></div>
      <div class="diag-row"><span class="diag-label">Config</span><span class="diag-value" style="font-size:10px">${esc(s.config_path)}</span></div>`;
  } catch (err) { $('#server-status-content').innerHTML = `<div class="form-error">${esc(err.detail)}</div>`; }
}

async function refreshServerConfigForm() {
  try {
    if (!state.settings || !state.settings.settings) state.settings = await api.get('/api/settings');
    const values = state.settings.settings;
    $('#server-config-form').innerHTML = CONFIG_FIELDS.map((field) => {
      const value = values[field.key];
      const id = `cfg-${field.key}`;
      if (field.type === 'checkbox') {
        return `<label class="check"><input type="checkbox" id="${id}" data-key="${field.key}" ${value ? 'checked' : ''}/> ${esc(field.label)}</label>`;
      }
      if (field.type === 'select') {
        return `<label>${esc(field.label)}<select id="${id}" data-key="${field.key}">${field.options.map(([v, label]) => `<option value="${v}" ${v === value ? 'selected' : ''}>${esc(label)}</option>`).join('')}</select></label>`;
      }
      return `<label>${esc(field.label)}<input type="${field.type}" id="${id}" data-key="${field.key}" value="${esc(value)}"/></label>`;
    }).join('');
  } catch (err) { $('#server-config-form').innerHTML = `<div class="form-error">${esc(err.detail)}</div>`; }
}

async function saveServerConfig() {
  const patch = {};
  $$('#server-config-form [data-key]').forEach((input) => {
    if (input.type === 'checkbox') patch[input.dataset.key] = input.checked;
    else if (input.type === 'number') patch[input.dataset.key] = Number(input.value);
    else patch[input.dataset.key] = input.value;
  });
  try {
    const result = await api.put('/api/settings', { values: patch });
    state.settings = { settings: result.settings };
    state.compatEnabled = !!result.settings.compat_layer;
    toast(result.restart_required ? 'Saved — restart the server to apply bind/TLS changes' : 'Saved', 'success');
  } catch (err) { toast(err.detail || err.message, 'error'); }
}

async function refreshSharedDirs() {
  try {
    const data = await api.get('/api/shared-dirs');
    const el = $('#shared-dir-list');
    const dirs = data.directories || [];
    el.innerHTML = dirs.length
      ? dirs.map((d) => `<div class="list-item"><div class="li-row"><span class="li-title" style="font-family:var(--font-mono);font-size:11px">${esc(d.path)}</span><button class="mini-btn danger" data-shared-delete="${d.id}">Remove</button></div></div>`).join('')
      : '<p class="side-hint">No shared directories. The transfer inbox is always available to authenticated users.</p>';
    $$('[data-shared-delete]', el).forEach((b) => b.addEventListener('click', async () => {
      await api.del(`/api/shared-dirs/${b.dataset.sharedDelete}`);
      await refreshSharedDirs();
    }));
  } catch (err) { $('#shared-dir-list').innerHTML = `<div class="form-error">${esc(err.detail)}</div>`; }
}

async function refreshServerLogs() {
  try {
    const data = await api.get('/api/logs?limit=80');
    $('#server-log').textContent = (data.logs || []).slice().reverse()
      .map((l) => `${new Date(l.ts * 1000).toLocaleTimeString()} [${l.level}] ${l.source}: ${l.message}`).join('\n');
  } catch (_) {}
}

/* ─────────────────────────── top bar & buttons ─────────────────────────── */
function buildNewTabMenu() {
  const menu = $('#new-tab-menu');
  const shells = (state.system && state.system.shells) || [];
  const header = '<div class="menu-head">Open a private terminal</div>';
  const items = shells.map((shell) => `
    <button class="menu-item" data-shell="${esc(shell.id)}">
      ${osBadge(shell.icon === 'windows' ? 'win32' : 'linux')}
      <span>${esc(shell.name)}</span>
      <small>${esc(shell.kind)}</small>
    </button>`).join('');
  const shared = state.user.role === 'owner'
    ? '<div class="menu-head">Collaborate</div><button class="menu-item" data-shared="1"><span class="pill pill-shared">SHARED</span><span>Shared session — everyone can join</span></button>'
    : '';
  menu.innerHTML = header + items + shared;

  $$('[data-shell]', menu).forEach((button) => button.addEventListener('click', () => {
    menu.classList.add('hidden');
    openNewTab(button.dataset.shell, 'local');
  }));
  $$('[data-shared]', menu).forEach((button) => button.addEventListener('click', () => {
    menu.classList.add('hidden');
    openSharedTab();
  }));
}

function wireTopBar() {
  $('#btn-new-tab').addEventListener('click', (event) => {
    event.stopPropagation();
    const menu = $('#new-tab-menu');
    buildNewTabMenu();
    menu.classList.toggle('hidden');
  });
  document.addEventListener('click', (event) => {
    if (!event.target.closest('.new-tab-wrap')) $('#new-tab-menu').classList.add('hidden');
  });
  $('#btn-empty-new-tab').addEventListener('click', () => openNewTab());

  $('#btn-diagnostics').addEventListener('click', showDiagnostics);
  $('#btn-refresh-diag').addEventListener('click', showDiagnostics);
  $('#btn-server-settings').addEventListener('click', showServerSettings);
  $('#btn-settings').addEventListener('click', () => {
    openModal('modal-settings');
  });

  $('#setting-font-size').addEventListener('input', (e) => {
    state.fontSize = Math.min(28, Math.max(9, +e.target.value || 13));
    for (const tab of state.tabs.values()) tab.setFontSize(state.fontSize);
    saveClientPrefs();
  });
  $('#setting-cursor').addEventListener('change', (e) => {
    state.cursorStyle = e.target.value;
    for (const tab of state.tabs.values()) tab.setCursor(state.cursorStyle, state.cursorBlink);
    saveClientPrefs();
  });
  $('#setting-cursor-blink').addEventListener('change', (e) => {
    state.cursorBlink = e.target.checked;
    for (const tab of state.tabs.values()) tab.setCursor(state.cursorStyle, state.cursorBlink);
    saveClientPrefs();
  });
  $('#setting-scrollback').addEventListener('change', (e) => {
    state.scrollback = Math.min(50000, Math.max(500, +e.target.value || 5000));
    for (const tab of state.tabs.values()) tab.setScrollback(state.scrollback);
    saveClientPrefs();
  });

  $('#btn-font-plus').addEventListener('click', () => {
    state.fontSize = Math.min(28, state.fontSize + 1);
    $('#setting-font-size').value = state.fontSize;
    for (const tab of state.tabs.values()) tab.setFontSize(state.fontSize);
    saveClientPrefs();
  });
  $('#btn-font-minus').addEventListener('click', () => {
    state.fontSize = Math.max(9, state.fontSize - 1);
    $('#setting-font-size').value = state.fontSize;
    for (const tab of state.tabs.values()) tab.setFontSize(state.fontSize);
    saveClientPrefs();
  });

  // server stop / restart
  $('#btn-stop-server').addEventListener('click', async () => {
    if (!confirm('Stop the PenguinTerm server on this machine? Connected users will be disconnected.')) return;
    try { await api.post('/api/server/shutdown', { confirm: true }); toast('Server is stopping…', 'warn'); }
    catch (err) { toast(err.detail || err.message, 'error'); }
  });
  $('#btn-restart-server').addEventListener('click', async () => {
    if (!confirm('Restart the PenguinTerm server? Connected users will briefly disconnect.')) return;
    try { await api.post('/api/server/restart', { confirm: true }); toast('Server is restarting…', 'warn'); }
    catch (err) { toast(err.detail || err.message, 'error'); }
  });
  $('#btn-save-config').addEventListener('click', saveServerConfig);

  $('#shared-dir-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const input = event.target.path;
    try {
      await api.post('/api/shared-dirs', { path: input.value.trim(), label: '' });
      input.value = '';
      await refreshSharedDirs();
      toast('Directory is now shared', 'success');
    } catch (err) { toast(err.detail || err.message, 'error'); }
  });

  // diagnostics & logs refresh handled in their own functions
}

/* search */
function wireSearch() {
  const bar = $('#searchbar');
  const input = $('#search-input');
  const active = () => (state.activeTab ? state.tabs.get(state.activeTab) : null);
  $('#btn-search').addEventListener('click', () => {
    bar.classList.toggle('hidden');
    if (!bar.classList.contains('hidden')) input.focus();
  });
  $('#search-close').addEventListener('click', () => bar.classList.add('hidden'));
  input.addEventListener('input', () => {
    const tab = active(); if (!tab) return;
    tab.searchAddon.findNext(input.value, { caseSensitive: false, regex: false });
  });
  $('#search-next').addEventListener('click', () => active() && active().searchAddon.findNext(input.value));
  $('#search-prev').addEventListener('click', () => active() && active().searchAddon.findPrevious(input.value));
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); active() && active().searchAddon.findNext(input.value); }
    if (event.key === 'Escape') bar.classList.add('hidden');
  });
}

/* transfers & peers */
function wireTransfersAndServers() {
  $('#upload-input').addEventListener('change', (event) => {
    const files = Array.from(event.target.files || []);
    if (files.length) handleUpload(files);
    event.target.value = '';
  });

  $('#btn-open-inbox').addEventListener('click', async () => {
    try {
      const s = await api.get('/api/server/status');
      const inbox = `${s.data_dir}\\transfers\\inbox`;
      await navigator.clipboard.writeText(inbox).catch(() => {});
      toast(`Inbox path copied to clipboard: ${inbox}`, 'success');
    } catch (err) { toast('Could not read server status', 'error'); }
  });

  $('#btn-add-server').addEventListener('click', () => {
    $('#probe-result').textContent = '';
    openModal('modal-add-server');
  });
  $('#btn-probe-server').addEventListener('click', async () => {
    const form = $('#add-server-form');
    const host = form.host.value.trim(); if (!host) return;
    $('#probe-result').textContent = 'Probing…';
    try {
      const result = await api.post('/api/servers/probe', {
        host, port: +form.port.value, tls: form.tls.checked,
      });
      $('#probe-result').textContent = result.reachable
        ? `✓ reachable, ${result.latency_ms} ms, PenguinTerm ${result.version || '?'} on ${result.platform || '?'}`
        : `✗ ${result.detail || 'unreachable'}`;
      $('#probe-result').style.color = result.reachable ? 'var(--green)' : 'var(--red)';
    } catch (err) { $('#probe-result').textContent = `✗ ${err.detail || err.message}`; }
  });
  $('#add-server-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      await api.post('/api/servers', {
        label: form.label.value.trim(),
        host: form.host.value.trim(),
        port: +form.port.value,
        tls: form.tls.checked,
        username: form.username.value.trim(),
      });
      closeModals();
      await refreshServers();
      toast('Server saved', 'success');
    } catch (err) { toast(err.detail || err.message, 'error'); }
  });

  $('#send-peer-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    const serverId = form.server_id.value;
    try {
      await api.post('/api/transfer/send-to-peer', {
        server_id: serverId ? +serverId : null,
        username: form.username.value,
        password: form.password.value,
        path: state.pendingSendPath,
      });
      closeModals();
      toast('File delivered to the peer inbox', 'success');
      await refreshTransfers();
    } catch (err) { toast(err.detail || err.message, 'error'); }
  });

  $('#btn-join-room').addEventListener('click', async () => {
    const input = $('#join-room-code');
    const code = input.value.trim(); if (!code) return;
    try {
      const room = await api.post('/api/rooms/join', { code });
      input.value = '';
      toast(`Joined room "${room.name}"`, 'success');
      await refreshRooms();
    } catch (err) { toast(err.detail || err.message, 'error'); }
  });
  $('#btn-create-room').addEventListener('click', () => openModal('modal-create-room'));
  $('#create-room-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      const room = await api.post('/api/rooms', { name: form.name.value.trim() });
      form.name.value = '';
      closeModals();
      await refreshRooms();
      toast(`Room created — join code ${room.code}`, 'success');
    } catch (err) { toast(err.detail || err.message, 'error'); }
  });

  $('#btn-create-user').addEventListener('click', () => openModal('modal-create-user'));
  $('#create-user-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      await api.post('/api/users', {
        username: form.username.value.trim(),
        password: form.password.value,
        role: form.role.value,
      });
      form.reset();
      closeModals();
      await refreshUsers();
      toast('User created', 'success');
    } catch (err) { toast(err.detail || err.message, 'error'); }
  });

  $('#btn-create-invite').addEventListener('click', () => {
    $('#invite-result').classList.add('hidden');
    openModal('modal-invite');
  });
  $('#create-invite-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      const invite = await api.post('/api/invites', {
        role: form.role.value,
        max_uses: +form.max_uses.value || 1,
        expires_hours: +form.expires_hours.value || 72,
      });
      $('#invite-url').value = invite.url;
      $('#invite-result').classList.remove('hidden');
      await refreshUsers();
      toast('Invitation created', 'success');
    } catch (err) { toast(err.detail || err.message, 'error'); }
  });
  $('#btn-copy-invite').addEventListener('click', async () => {
    const input = $('#invite-url');
    input.select();
    try { await navigator.clipboard.writeText(input.value); toast('Invitation link copied', 'success'); }
    catch { document.execCommand('copy'); }
  });
}

/* ─────────────────────────── shortcuts ─────────────────────────── */
function wireShortcuts() {
  document.addEventListener('keydown', (event) => {
    const mod = event.ctrlKey || event.metaKey;
    if (!mod) return;
    const key = event.key.toLowerCase();
    if (event.shiftKey && key === 't') { event.preventDefault(); openNewTab(); return; }
    if (event.shiftKey && key === 'w') { event.preventDefault(); if (state.activeTab) closeTab(state.activeTab); return; }
    if (event.shiftKey && key === 'f') { event.preventDefault(); $('#searchbar').classList.toggle('hidden'); if (!$('#searchbar').classList.contains('hidden')) $('#search-input').focus(); return; }
    if (key === 'tab' && !event.shiftKey) { event.preventDefault(); cycleTab(+1); return; }
    if (key === 'tab' && event.shiftKey) { event.preventDefault(); cycleTab(-1); return; }
    if (key === '=' || key === '+') { event.preventDefault(); $('#btn-font-plus').click(); return; }
    if (key === '-') { event.preventDefault(); $('#btn-font-minus').click(); return; }
  });
}

/* ─────────────────────────── boot ─────────────────────────── */
wireAuthForms();
wireSidebar();
wireTopBar();
wireModals();
wireSearch();
wireTransfersAndServers();
wireShortcuts();
bootstrap();
