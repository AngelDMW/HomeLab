(() => {
  'use strict';

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const state = {
    user: null,
    net: localStorage.getItem('nexus-network') || 'lan',
    tab: 'services',
    category: 'Todos',
    search: '',
    items: [],
    runbooks: [],
    notes: [],
    statuses: {},
    statusTimer: null,
  };

  const esc = (str = '') => String(str)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');

  const fmtDate = (iso) => {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      return d.toLocaleString('es-DO', { dateStyle: 'medium', timeStyle: 'short' });
    } catch { return iso; }
  };

  function toast(msg, ms = 2600) {
    const t = $('#toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => t.classList.remove('show'), ms);
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
      ...opts,
    });
    const text = await res.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
    if (!res.ok) {
      const err = new Error(data.detail || data.error || `HTTP ${res.status}`);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  // --- Auth ---
  async function checkAuth() {
    try {
      const me = await api('/api/me');
      state.user = me.user;
      showApp();
      await loadAll();
    } catch (e) {
      showLogin();
    }
  }

  function showLogin() {
    $('#login-view').classList.remove('hidden');
    $('#app-view').classList.add('hidden');
  }

  function showApp() {
    $('#login-view').classList.add('hidden');
    $('#app-view').classList.remove('hidden');
    applyNetworkUI();
  }

  $('#login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = $('#login-btn');
    const err = $('#login-error');
    err.textContent = '';
    btn.disabled = true;
    btn.textContent = 'Entrando...';
    try {
      const username = $('#login-user').value.trim();
      const password = $('#login-pass').value;
      const data = await api('/api/login', { method: 'POST', body: JSON.stringify({ username, password }) });
      state.user = data.user;
      $('#login-pass').value = '';
      showApp();
      await loadAll();
    } catch (e2) {
      err.textContent = e2.message || 'No se pudo iniciar sesión';
    } finally {
      btn.disabled = false;
      btn.textContent = 'Entrar';
    }
  });

  async function logout() {
    try { await api('/api/logout', { method: 'POST' }); } catch {}
    state.items = []; state.runbooks = []; state.notes = []; state.statuses = {};
    showLogin();
  }
  $('#logout-btn').addEventListener('click', logout);
  $('#logout-btn-side').addEventListener('click', logout);

  // --- Network toggle ---
  function applyNetworkUI() {
    const toggle = $('#network-toggle');
    toggle.dataset.net = state.net;
    $('#net-label-side').textContent = state.net === 'lan' ? 'LAN Casa' : 'Tailscale';
    $$('.net-dot').forEach(d => {
      d.style.background = state.net === 'lan' ? 'var(--green)' : '#5eb0ff';
      d.style.boxShadow = state.net === 'lan' ? '0 0 0 4px rgba(48,209,88,0.12)' : '0 0 0 4px rgba(94,176,255,0.12)';
    });
  }

  $('#network-toggle').addEventListener('click', () => {
    state.net = state.net === 'lan' ? 'vpn' : 'lan';
    localStorage.setItem('nexus-network', state.net);
    applyNetworkUI();
    renderServices();
  });

  function activeUrl(item) {
    const u = state.net === 'vpn' ? (item.vpn_url || item.lan_url || '') : (item.lan_url || item.vpn_url || '');
    return u;
  }

  // --- Tabs ---
  function setTab(tab) {
    state.tab = tab;
    $$('.nav-btn, .bottom-nav-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    $$('.tab-panel').forEach(p => p.classList.toggle('active', p.id === `tab-${tab}`));
    render();
  }
  $$('.nav-btn, .bottom-nav-btn').forEach(b => b.addEventListener('click', () => setTab(b.dataset.tab)));

  // --- Search / Cmd+K ---
  const searchInput = $('#global-search');
  searchInput.addEventListener('input', (e) => { state.search = e.target.value.toLowerCase().trim(); render(); });
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      searchInput.focus();
      searchInput.select();
    }
    if (e.key === 'Escape') { closeModal(); searchInput.blur(); }
  });

  // --- Data loading ---
  async function loadAll() {
    await Promise.all([loadItems(), loadRunbooks(), loadNotes()]);
    render();
    refreshStatus();
    if (state.statusTimer) clearInterval(state.statusTimer);
    state.statusTimer = setInterval(refreshStatus, 60000);
  }

  async function loadItems() {
    const data = await api('/api/items');
    state.items = data.items || [];
  }
  async function loadRunbooks() {
    const data = await api('/api/runbooks');
    state.runbooks = data.runbooks || [];
  }
  async function loadNotes() {
    const data = await api('/api/notes');
    state.notes = data.notes || [];
  }

  async function refreshStatus() {
    const btn = $('#refresh-status');
    btn.classList.add('spinning');
    try {
      const data = await api('/api/status-all');
      state.statuses = data.statuses || {};
      renderServices();
    } catch (e) {
      // ignore
    } finally {
      btn.classList.remove('spinning');
    }
  }
  $('#refresh-status').addEventListener('click', refreshStatus);

  // --- Render ---
  function render() {
    renderServices();
    renderRunbooks();
    renderNotes();
  }

  function matchesSearch(item, fields) {
    if (!state.search) return true;
    const hay = fields.map(f => (item[f] || '')).join(' ').toLowerCase();
    return hay.includes(state.search);
  }

  function renderServices() {
    const cats = ['Todos', ...new Set(state.items.map(i => i.category))];
    const filters = $('#category-filters');
    filters.innerHTML = cats.map(c => `<button class="filter-chip ${state.category===c?'active':''}" data-cat="${esc(c)}">${esc(c)}</button>`).join('');
    $$('.filter-chip', filters).forEach(b => b.addEventListener('click', () => { state.category = b.dataset.cat; renderServices(); }));

    const q = state.search;
    let filtered = state.items.filter(i => matchesSearch(i, ['name','category','username','notes','lan_url','vpn_url']));
    if (state.category !== 'Todos') filtered = filtered.filter(i => i.category === state.category);

    const favs = filtered.filter(i => i.is_favorite);
    const favBlock = $('#favorites-block');
    favBlock.classList.toggle('hidden', favs.length === 0);
    $('#favorites-grid').innerHTML = favs.map(serviceCard).join('');
    $('#services-grid').innerHTML = filtered.map(serviceCard).join('');
    $('#services-empty').classList.toggle('hidden', filtered.length !== 0);
    $('#all-services-head').textContent = state.category === 'Todos' ? 'Todos los servicios' : state.category;

    bindServiceCardEvents($('#favorites-grid'));
    bindServiceCardEvents($('#services-grid'));
  }

  function statusClass(id) {
    const s = state.statuses[String(id)] ?? state.statuses[id];
    if (s === 'online') return 'online';
    if (s === 'offline') return 'offline';
    return '';
  }

  function serviceCard(item) {
    const url = activeUrl(item);
    const st = statusClass(item.id);
    const canOpen = !!url;
    const metaUser = item.username ? `<div class="meta-row"><span class="meta-label">Usuario:</span> ${esc(item.username)}</div>` : '';
    const metaUrl = url ? `<div class="meta-row"><span class="meta-label">${state.net === 'vpn' ? 'VPN' : 'LAN'}:</span> ${esc(url)}</div>` : `<div class="meta-row"><span class="meta-label">URL:</span> No disponible en esta red</div>`;
    return `
      <article class="service-card glass" data-id="${item.id}">
        <div class="card-top">
          <div class="card-ident">
            <div class="card-icon">${esc(item.icon || '🔗')}</div>
            <div style="min-width:0">
              <div class="card-name" title="${esc(item.name)}">${esc(item.name)}</div>
              <div class="card-cat">${esc(item.category)}</div>
            </div>
          </div>
          <div class="card-top-right">
            <span class="status-dot ${st}" title="${st || 'sin estado'}"></span>
            <button class="fav-btn ${item.is_favorite ? 'active' : ''}" data-action="fav" title="Favorito">${item.is_favorite ? '★' : '☆'}</button>
          </div>
        </div>
        <div class="card-meta">${metaUser}${metaUrl}</div>
        <div class="card-actions">
          <button class="chip-btn primary" data-action="open" ${canOpen ? '' : 'disabled'}>Abrir Panel</button>
          <button class="chip-btn" data-action="copy-user" ${item.username ? '' : 'disabled'}>Copiar Usuario</button>
          <button class="chip-btn" data-action="copy-pass" ${item.has_password ? '' : 'disabled'}>Copiar Contraseña</button>
          <button class="chip-btn" data-action="edit">Ver / Editar</button>
        </div>
      </article>`;
  }

  function bindServiceCardEvents(container) {
    $$('.service-card', container).forEach(card => {
      const id = Number(card.dataset.id);
      card.addEventListener('click', async (e) => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        const action = btn.dataset.action;
        const item = state.items.find(i => i.id === id);
        if (!item) return;
        if (action === 'open') {
          const url = activeUrl(item);
          if (url) window.open(url, '_blank', 'noopener,noreferrer');
        } else if (action === 'copy-user') {
          await copyText(item.username || '', 'Usuario copiado');
        } else if (action === 'copy-pass') {
          await copyPassword(item.id);
        } else if (action === 'edit') {
          openItemModal(item);
        } else if (action === 'fav') {
          await toggleFavorite(item);
        }
      });
    });
  }

  async function copyText(text, okMsg = 'Copiado') {
    if (!text) return;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement('textarea');
        ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.focus(); ta.select();
        document.execCommand('copy'); document.body.removeChild(ta);
      }
      toast(okMsg);
    } catch {
      toast('No se pudo copiar');
    }
  }

  async function copyPassword(id) {
    try {
      const data = await api(`/api/items/${id}/reveal`, { method: 'POST' });
      const pw = data.password || '';
      await copyText(pw, 'Copiado · se limpiará en 30 s');
      setTimeout(async () => {
        try {
          if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText('');
        } catch {}
      }, 30000);
    } catch (e) {
      toast('No se pudo revelar la contraseña');
    }
  }

  async function toggleFavorite(item) {
    try {
      const payload = { ...item, password: '', is_favorite: !item.is_favorite };
      await api(`/api/items/${item.id}`, { method: 'PUT', body: JSON.stringify(payload) });
      await loadItems();
      renderServices();
    } catch (e) {
      toast('No se pudo actualizar favorito');
    }
  }

  // --- Modal ---
  function openModal(title, bodyHtml) {
    $('#modal-title').textContent = title;
    $('#modal-body').innerHTML = bodyHtml;
    $('#modal-backdrop').classList.remove('hidden');
  }
  function closeModal() { $('#modal-backdrop').classList.add('hidden'); $('#modal-body').innerHTML = ''; }
  $('#modal-close').addEventListener('click', closeModal);
  $('#modal-backdrop').addEventListener('click', (e) => { if (e.target.id === 'modal-backdrop') closeModal(); });

  function openItemModal(item = null) {
    const isEdit = !!item;
    const v = item || { name:'', category:'Infraestructura & SSH', icon:'🔗', lan_url:'', vpn_url:'', health_url:'', username:'', notes:'', is_favorite:false };
    openModal(isEdit ? 'Editar servicio / credencial' : 'Añadir servicio / credencial', `
      <form id="item-form" class="modal-form">
        <div class="form-grid">
          <label class="field"><span>Nombre</span><input name="name" required maxlength="120" value="${esc(v.name)}"></label>
          <label class="field"><span>Categoría</span>
            <select name="category">
              ${['IA & Control','Multimedia','Nube & Fotos','Red & Seguridad','Infraestructura & SSH'].map(c => `<option value="${esc(c)}" ${v.category===c?'selected':''}>${esc(c)}</option>`).join('')}
            </select>
          </label>
          <label class="field"><span>Icono (emoji)</span><input name="icon" maxlength="16" value="${esc(v.icon||'🔗')}"></label>
          <label class="field"><span>Usuario</span><input name="username" maxlength="200" value="${esc(v.username||'')}" autocomplete="off"></label>
          <label class="field full"><span>URL LAN</span><input name="lan_url" maxlength="500" value="${esc(v.lan_url||'')}" placeholder="http://192.168.1.x:puerto"></label>
          <label class="field full"><span>URL Tailscale / VPN</span><input name="vpn_url" maxlength="500" value="${esc(v.vpn_url||'')}" placeholder="http://100.x.y.z:puerto"></label>
          <label class="field full"><span>URL de health check</span><input name="health_url" maxlength="500" value="${esc(v.health_url||'')}" placeholder="http://..., https://... o tcp://host:puerto"></label>
          <label class="field full"><span>${isEdit ? 'Nueva contraseña (dejar vacío para mantener)' : 'Contraseña'}</span><input name="password" type="password" maxlength="4000" autocomplete="new-password" placeholder="${isEdit ? '••••••••' : 'Configurar en bóveda'}"></label>
          <label class="field full"><span>Notas</span><textarea name="notes" maxlength="8000" rows="4">${esc(v.notes||'')}</textarea></label>
        </div>
        <label class="check-row"><input type="checkbox" name="is_favorite" ${v.is_favorite?'checked':''}> Marcar como favorito</label>
        <div class="modal-actions">
          ${isEdit ? `<button type="button" class="btn btn-danger" id="item-delete">Eliminar</button>` : ''}
          <button type="button" class="btn btn-secondary" id="item-cancel">Cancelar</button>
          <button type="submit" class="btn btn-primary">${isEdit ? 'Guardar cambios' : 'Crear servicio'}</button>
        </div>
      </form>
    `);

    $('#item-cancel').addEventListener('click', closeModal);
    if (isEdit) {
      $('#item-delete').addEventListener('click', async () => {
        if (!confirm(`¿Eliminar "${item.name}" de la bóveda?`)) return;
        try {
          await api(`/api/items/${item.id}`, { method: 'DELETE' });
          closeModal(); await loadItems(); renderServices(); toast('Servicio eliminado');
        } catch (e) { toast('No se pudo eliminar'); }
      });
    }
    $('#item-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const payload = {
        name: fd.get('name').trim(),
        category: fd.get('category'),
        icon: fd.get('icon').trim() || '🔗',
        lan_url: fd.get('lan_url').trim(),
        vpn_url: fd.get('vpn_url').trim(),
        health_url: fd.get('health_url').trim(),
        username: fd.get('username').trim(),
        password: fd.get('password'),
        notes: fd.get('notes').trim(),
        is_favorite: fd.get('is_favorite') === 'on',
      };
      try {
        if (isEdit) await api(`/api/items/${item.id}`, { method: 'PUT', body: JSON.stringify(payload) });
        else await api('/api/items', { method: 'POST', body: JSON.stringify(payload) });
        closeModal(); await loadItems(); renderServices(); refreshStatus();
        toast(isEdit ? 'Servicio actualizado' : 'Servicio creado');
      } catch (err) { toast(err.message || 'Error al guardar'); }
    });
  }

  $('#add-item-btn').addEventListener('click', () => openItemModal(null));

  // --- Runbooks ---
  function renderRunbooks() {
    const q = state.search;
    const filtered = state.runbooks.filter(r => !q || [r.title,r.description,r.command,r.category].join(' ').toLowerCase().includes(q));
    $('#runbooks-grid').innerHTML = filtered.map(r => `
      <article class="runbook-card glass">
        <div class="runbook-head"><strong>${esc(r.title)}</strong><span>${esc(r.category || 'Acción')}</span></div>
        <div class="runbook-desc">${esc(r.description || '')}</div>
        ${r.command ? `<div class="runbook-cmd">${esc(r.command)}</div>` : ''}
        <div class="runbook-actions">
          ${r.command ? `<button class="chip-btn" data-rb-copy="${r.id}">Copiar comando</button>` : ''}
          ${r.action_type === 'http' ? `<button class="chip-btn primary" data-rb-run="${r.id}">Ejecutar</button>` : ''}
        </div>
      </article>`).join('') || `<div class="empty-state">No hay runbooks que coincidan.</div>`;

    $$('[data-rb-copy]').forEach(b => b.addEventListener('click', async () => {
      const rb = state.runbooks.find(x => x.id === Number(b.dataset.rbCopy));
      if (rb) await copyText(rb.command, 'Comando copiado');
    }));
    $$('[data-rb-run]').forEach(b => b.addEventListener('click', () => runRunbook(Number(b.dataset.rbRun), b)));
  }

  async function runRunbook(id, btn) {
    const rb = state.runbooks.find(r => r.id === id);
    const feedback = $('#runbook-feedback');
    if (btn) { btn.disabled = true; btn.textContent = 'Ejecutando...'; }
    feedback.className = 'runbook-feedback';
    feedback.textContent = `Ejecutando: ${rb ? rb.title : id}...`;
    feedback.classList.remove('hidden');
    try {
      const data = await api(`/api/runbooks/${id}/run`, { method: 'POST' });
      if (data.ok) {
        feedback.classList.add('ok');
        feedback.textContent = `✔ ${rb?.title || 'Acción'}\nEstado: ${data.status_code || 'OK'}\n${(data.response || '').slice(0, 800)}`;
      } else {
        feedback.classList.add('err');
        feedback.textContent = `✖ ${rb?.title || 'Acción'}\n${data.error || data.response || 'Error desconocido'}`;
      }
    } catch (e) {
      feedback.classList.add('err');
      feedback.textContent = `✖ ${rb?.title || 'Acción'}\n${e.message}`;
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Ejecutar'; }
    }
  }

  $$('.action-runbook').forEach(card => card.addEventListener('click', async () => {
    const title = card.dataset.runbookTitle;
    const rb = state.runbooks.find(r => r.title === title);
    if (!rb) { toast('Runbook no encontrado'); return; }
    card.classList.add('loading');
    const go = card.querySelector('.action-go');
    const prev = go.textContent;
    go.textContent = 'Ejecutando...';
    await runRunbook(rb.id, null);
    go.textContent = prev;
    card.classList.remove('loading');
  }));

  // --- Notes ---
  function renderNotes() {
    const q = state.search;
    const filtered = state.notes.filter(n => !q || [n.title,n.content].join(' ').toLowerCase().includes(q));
    $('#notes-grid').innerHTML = filtered.map(n => `
      <article class="note-card glass">
        <div class="note-head"><h3>${esc(n.title)}</h3><span class="note-date">${esc(fmtDate(n.updated_at))}</span></div>
        <div class="note-content">${esc(n.content || '')}</div>
        <div class="note-actions">
          <button class="chip-btn" data-note-edit="${n.id}">Editar</button>
          <button class="chip-btn" data-note-del="${n.id}">Eliminar</button>
        </div>
      </article>`).join('') || `<div class="empty-state">No hay notas. Crea la primera para tu bitácora técnica.</div>`;

    $$('[data-note-edit]').forEach(b => b.addEventListener('click', () => {
      const note = state.notes.find(n => n.id === Number(b.dataset.noteEdit));
      if (note) openNoteModal(note);
    }));
    $$('[data-note-del]').forEach(b => b.addEventListener('click', async () => {
      const id = Number(b.dataset.noteDel);
      const note = state.notes.find(n => n.id === id);
      if (!confirm(`¿Eliminar nota "${note?.title || ''}"?`)) return;
      try { await api(`/api/notes/${id}`, { method: 'DELETE' }); await loadNotes(); renderNotes(); toast('Nota eliminada'); }
      catch { toast('No se pudo eliminar la nota'); }
    }));
  }

  function openNoteModal(note = null) {
    const isEdit = !!note;
    openModal(isEdit ? 'Editar nota' : 'Nueva nota', `
      <form id="note-form" class="modal-form">
        <label class="field"><span>Título</span><input name="title" required maxlength="200" value="${esc(note?.title || '')}"></label>
        <label class="field"><span>Contenido</span><textarea name="content" rows="10" maxlength="20000">${esc(note?.content || '')}</textarea></label>
        <div class="modal-actions">
          <button type="button" class="btn btn-secondary" id="note-cancel">Cancelar</button>
          <button type="submit" class="btn btn-primary">${isEdit ? 'Guardar' : 'Crear nota'}</button>
        </div>
      </form>`);
    $('#note-cancel').addEventListener('click', closeModal);
    $('#note-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const payload = { title: fd.get('title').trim(), content: fd.get('content') };
      try {
        if (isEdit) await api(`/api/notes/${note.id}`, { method: 'PUT', body: JSON.stringify(payload) });
        else await api('/api/notes', { method: 'POST', body: JSON.stringify(payload) });
        closeModal(); await loadNotes(); renderNotes(); toast(isEdit ? 'Nota actualizada' : 'Nota creada');
      } catch (err) { toast(err.message || 'Error al guardar nota'); }
    });
  }
  $('#add-note-btn').addEventListener('click', () => openNoteModal(null));

  // --- SW ---
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js').catch(() => {}); });
  }

  // --- Init ---
  applyNetworkUI();
  checkAuth();
})();
