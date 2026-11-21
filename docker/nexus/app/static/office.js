/* ============================================================
   HomeLab Nexus · Oficina IA (HomeLab Corp Live)
   SVG animado + Canvas mental + CSS puro. Cero dependencias.
   Datos: /api/corp-office-state (Corp ledger + Sentinel AI).
   ============================================================ */
(() => {
  'use strict';

  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => Array.from(root.querySelectorAll(s));
  const esc = (value = '') => String(value)
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
  const nz = (value, fallback) => (value === undefined || value === null ? fallback : value);

  const STATES = {
    idle_guard: { label: 'EN GUARDIA', color: '#8b97a8', icon: '◉' },
    working:    { label: 'TRABAJANDO', color: '#5eb0ff', icon: '⌘' },
    reporting:  { label: 'INFORMANDO', color: '#30d158', icon: '✎' },
    healing:    { label: 'REPARANDO', color: '#ff9f0a', icon: '⚙' },
    alert:      { label: 'ALERTA', color: '#ff453a', icon: '!' },
  };

  const ACCENTS = {
    director: '#5eb0ff', infra: '#ff9f0a', secops: '#30d158', media: '#ff6b9d',
    cloud: '#64d2ff', updater: '#bf5af2', backup: '#ffd60a', architect: '#8e8eff',
    qa: '#4ade80',
  };
  const NAMES = {
    director: 'HomeLab-Director', infra: 'Infra', secops: 'SecOps', media: 'Media',
    cloud: 'Cloud', updater: 'Updater', backup: 'Backup', architect: 'Architect', qa: 'QA',
  };

  // Orden de los 8 puestos (4 columnas x 2 filas) en el viewBox 1000x520.
  // Geometría compacta para que quepa completa en 1280x800 sin scroll.
  const ORDER = ['infra', 'secops', 'media', 'cloud', 'updater', 'backup', 'architect', 'qa'];
  const COLS = [140, 380, 620, 860];
  const ROW_Y = [132, 316];
  const VIEW_W = 1000;
  const VIEW_H = 520;
  const STATION_W = 206;
  const STATION_H = 176;
  // El burbujeo va ENCIMA del avatar: separado del título y con la cola apuntando al personaje.
  const BUBBLE_DY = 88;
  const ACTOR_DY = 166;

  let selectedId = null;
  let timer = null;
  let running = false;
  let inFlight = false;
  let stageBuilt = false;
  let lastData = null;

  function notify(message, ms = 3200) {
    const toast = $('#toast');
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add('show');
    clearTimeout(notify._t);
    notify._t = setTimeout(() => toast.classList.remove('show'), ms);
  }

  function fmtDate(iso) {
    if (!iso) return '—';
    try {
      return new Date(iso).toLocaleString('es-DO', { dateStyle: 'medium', timeStyle: 'short' });
    } catch (err) { return String(iso); }
  }

  /* ---------------- Avatar vectorial por rol ---------------- */
  function avatarSvg(id, state, scale = 1) {
    const accent = ACCENTS[id] || '#5eb0ff';
    let back = '';
    let front = '';

    if (id === 'director') {
      back = `<path d="M-16 -28 Q-38 -12 -28 2 L-16 0 Z" fill="rgba(10,132,255,.18)" stroke="${accent}" stroke-width="1.1"/>
              <rect x="24" y="-42" width="18" height="26" rx="3" fill="rgba(10,132,255,.10)" stroke="${accent}" stroke-width="1"/>`;
      front = `<path d="M-14 -54 Q0 -62 14 -54 L12 -47 Q0 -53 -12 -47 Z" fill="rgba(94,176,255,.28)" stroke="${accent}" stroke-width="1.4"/>
               <path d="M-3 -62 L0 -68 L3 -62" fill="none" stroke="${accent}" stroke-width="1.4"/>
               <path d="M-9 -20 L9 -20" stroke="${accent}" stroke-width="2" stroke-linecap="round"/>`;
    } else if (id === 'infra') {
      back = `<rect x="-42" y="-52" width="18" height="50" rx="3" fill="#111119" stroke="${accent}" stroke-width="1"/>
              <rect x="24" y="-52" width="18" height="50" rx="3" fill="#111119" stroke="${accent}" stroke-width="1"/>
              <circle cx="-33" cy="-44" r="1.8" fill="#30d158"/><circle cx="-33" cy="-36" r="1.8" fill="#30d158"/>
              <circle cx="33" cy="-44" r="1.8" fill="#ff453a"/><circle cx="33" cy="-36" r="1.8" fill="#30d158"/>`;
      front = `<path d="M-14 -53 Q0 -65 14 -53 L15 -49 L-15 -49 Z" fill="#ff9f0a" stroke="#ffcf85" stroke-width="1"/>
               <path d="M-18 -49 L18 -49" stroke="#ffcf85" stroke-width="2" stroke-linecap="round"/>`;
    } else if (id === 'secops') {
      back = `<path d="M-38 -50 A38 38 0 0 1 38 -50" fill="none" stroke="${accent}" stroke-width="1" opacity=".5"/>
              <path d="M-26 -50 A26 26 0 0 1 26 -50" fill="none" stroke="${accent}" stroke-width="1" opacity=".7"/>
              <path d="M0 -50 L0 -74" stroke="${accent}" stroke-width="1" opacity=".8"/>`;
      front = `<path d="M0 -32 L15 -26 L13 -9 Q0 2 -13 -9 L-15 -26 Z" fill="rgba(48,209,88,.18)" stroke="${accent}" stroke-width="1.8"/>
               <path d="M-5 -18 L-1 -14 L6 -22" fill="none" stroke="#b9ffd0" stroke-width="1.8" stroke-linecap="round"/>`;
    } else if (id === 'media') {
      back = `<rect x="22" y="-46" width="24" height="20" rx="2" fill="rgba(255,107,157,.10)" stroke="${accent}" stroke-width="1"/>
              <path d="M27 -39 L40 -39 M27 -34 L36 -34" stroke="${accent}" stroke-width="1.2" opacity=".7"/>`;
      front = `<g transform="translate(0,-64)"><rect x="-17" y="-3" width="34" height="9" rx="1.5" fill="#1c1c28" stroke="${accent}" stroke-width="1.2"/>
               <path d="M-13 -3 L-8 6 M-5 -3 L0 6 M3 -3 L8 6 M11 -3 L16 6" stroke="${accent}" stroke-width="2.4" opacity=".8"/></g>`;
    } else if (id === 'cloud') {
      back = `<path d="M-40 -44 A11 11 0 0 1 -20 -50 A14 14 0 0 1 4 -52 A12 12 0 0 1 22 -43 L22 -34 L-40 -34 Z"
                    fill="rgba(100,210,255,.10)" stroke="${accent}" stroke-width="1"/>`;
      front = `<rect x="12" y="-30" width="20" height="15" rx="3" fill="#1c1c28" stroke="${accent}" stroke-width="1.4"/>
               <circle cx="22" cy="-22.5" r="4" fill="none" stroke="${accent}" stroke-width="1.4"/>
               <circle cx="34" cy="-33" r="2.4" fill="${accent}"/>`;
    } else if (id === 'updater') {
      back = `<circle cx="-30" cy="-38" r="13" fill="none" stroke="${accent}" stroke-width="4" stroke-dasharray="5 5" opacity=".8"/>
              <circle cx="-30" cy="-38" r="4" fill="none" stroke="${accent}" stroke-width="1.5"/>`;
      front = `<path d="M21 -34 A13 13 0 1 0 31 -22" fill="none" stroke="${accent}" stroke-width="3" stroke-linecap="round"/>
               <path d="M31 -31 L31 -21 L21 -21" fill="none" stroke="${accent}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>`;
    } else if (id === 'backup') {
      back = `<rect x="-42" y="-50" width="26" height="46" rx="4" fill="#171720" stroke="${accent}" stroke-width="1.4"/>
              <circle cx="-29" cy="-27" r="7" fill="none" stroke="${accent}" stroke-width="1.4"/>
              <path d="M-29 -31 L-29 -27 L-26 -25" stroke="${accent}" stroke-width="1.2" fill="none"/>
              <rect x="26" y="-24" width="18" height="8" rx="2" fill="#171720" stroke="${accent}" stroke-width="1"/>
              <rect x="26" y="-12" width="18" height="8" rx="2" fill="#171720" stroke="${accent}" stroke-width="1"/>`;
      front = `<rect x="-13" y="-38" width="26" height="18" rx="3" fill="rgba(255,214,10,.08)" stroke="${accent}" stroke-width="1.4"/>
               <path d="M-6 -29 L-2 -25 L7 -34" fill="none" stroke="#fff4b0" stroke-width="2" stroke-linecap="round"/>`;
    } else if (id === 'architect') {
      back = `<rect x="-42" y="-54" width="26" height="34" rx="2" fill="rgba(142,142,255,.08)" stroke="${accent}" stroke-width="1"/>
              <path d="M-38 -47 L-20 -47 M-38 -41 L-20 -41 M-38 -35 L-20 -35 M-34 -52 L-34 -22 M-26 -52 L-26 -22"
                    stroke="${accent}" stroke-width=".7" opacity=".6"/>`;
      front = `<path d="M-38 -14 L38 -30" stroke="${accent}" stroke-width="2.2" stroke-linecap="round"/>
               <path d="M-32 -16 L-32 -12 M-20 -18 L-20 -14 M-8 -21 L-8 -17 M4 -23 L4 -19 M16 -26 L16 -22 M28 -28 L28 -24"
                    stroke="${accent}" stroke-width="1" opacity=".8"/>`;
    } else if (id === 'qa') {
      back = `<rect x="-42" y="-50" width="22" height="30" rx="3" fill="rgba(74,222,128,.07)" stroke="${accent}" stroke-width="1"/>
              <path d="M-37 -43 L-33 -43 M-37 -36 L-33 -36 M-37 -29 L-33 -29" stroke="${accent}" stroke-width="1.2"/>
              <path d="M-30 -43 L-27 -40 L-23 -45" fill="none" stroke="${accent}" stroke-width="1.2"/>`;
      front = `<circle cx="29" cy="-37" r="10" fill="rgba(74,222,128,.07)" stroke="${accent}" stroke-width="2"/>
               <path d="M22 -30 L15 -22" stroke="${accent}" stroke-width="3" stroke-linecap="round"/>
               <path d="M25 -37 L28 -34 L33 -40" fill="none" stroke="#d6ffe4" stroke-width="1.6" stroke-linecap="round"/>`;
    }

    const glow = (STATES[state] || STATES.idle_guard).color;
    return `<g transform="scale(${scale})"><g class="actor-body">
      <ellipse cx="0" cy="2" rx="21" ry="4" fill="rgba(0,0,0,.5)"/>
      ${back}
      <path d="M-18 0 L-16 -30 Q0 -40 16 -30 L18 0 Z" fill="#141420" stroke="${accent}" stroke-width="1.8"/>
      <rect x="-12" y="-34" width="24" height="20" rx="8" fill="#1c1c28" stroke="${accent}" stroke-width="1.2"/>
      <circle cx="0" cy="-48" r="13" fill="#232333" stroke="${accent}" stroke-width="1.8"/>
      <circle cx="-4.5" cy="-49" r="1.7" fill="${glow}"/><circle cx="4.5" cy="-49" r="1.7" fill="${glow}"/>
      <path d="M-5 -42 Q0 -39 5 -42" fill="none" stroke="${accent}" stroke-width="1.4" stroke-linecap="round"/>
      ${front}
    </g></g>`;
  }

  function miniAvatar(id, state) {
    return `<svg class="mini-avatar" viewBox="-48 -80 96 90" aria-hidden="true">
      ${avatarSvg(id, state, 1)}
    </svg>`;
  }

  /* ---------------- Escenario ---------------- */
  function buildStage() {
    const stage = $('#office-stage');
    if (!stage || stageBuilt) return;
    let svg = `<svg viewBox="0 0 ${VIEW_W} ${VIEW_H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="HomeLab Corp HQ">
      <defs>
        <linearGradient id="hqDesk" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="rgba(255,255,255,.06)"/>
          <stop offset="1" stop-color="rgba(255,255,255,.015)"/>
        </linearGradient>
      </defs>
      <g class="hq-grid">`;
    for (let x = 0; x <= VIEW_W; x += 50) svg += `<line x1="${x}" y1="0" x2="${x}" y2="${VIEW_H}"/>`;
    for (let y = 0; y <= VIEW_H; y += 50) svg += `<line x1="0" y1="${y}" x2="${VIEW_W}" y2="${y}"/>`;
    svg += `</g>
      <rect class="hq-floor" x="14" y="6" width="972" height="${VIEW_H - 14}" rx="22"/>
      <text class="hq-sub" x="30" y="18">HOMELAB CORP HQ · PLANTILLA 9 · NODO 1 / NODO 2</text>`;

    // Cabina del Director (y=24..124). Título, punto de estado y los 8 puestos
    // tienen líneas propias: el indicador nunca toca la "R" de DIRECTOR.
    svg += `<rect x="300" y="24" width="400" height="100" rx="16" fill="url(#hqDesk)" stroke="rgba(94,176,255,.28)"/>
      <text class="hq-station-no" x="318" y="44">00 · HOME LAB-DIRECTOR</text>
      <circle id="director-dot" cx="482" cy="41" r="4.5" fill="#30d158" stroke="rgba(5,5,7,.9)" stroke-width="1.5"/>
      <text class="hq-sub" x="318" y="60">DIRECCIÓN &amp; COORDINACIÓN</text>
      <text class="hq-status-text" data-status="director" x="318" y="82" text-anchor="start" fill="#8b97a8">◉ EN GUARDIA</text>
      <rect x="410" y="118" width="180" height="6" rx="3" fill="rgba(10,132,255,.16)" stroke="rgba(94,176,255,.35)"/>`;

    // Líneas de datos del Director a las 8 estaciones
    ORDER.forEach((id, index) => {
      const cx = COLS[index % 4];
      const rowY = ROW_Y[Math.floor(index / 4)];
      const endY = rowY + 6;
      const midY = (124 + endY) / 2;
      const d = `M 500 124 Q 500 ${midY} ${cx} ${endY}`;
      svg += `<path id="link-${id}" class="hq-data-line" d="${d}"/>
        <circle class="hq-particle" r="2.7"><animateMotion dur="${2.6 + (index % 3) * .5}s" begin="-${index * .33}s" repeatCount="indefinite"><mpath href="#link-${id}"/></animateMotion></circle>
        <circle class="hq-particle" r="1.8" opacity=".65"><animateMotion dur="${3.4 + (index % 2) * .4}s" begin="-${1.1 + index * .2}s" repeatCount="indefinite"><mpath href="#link-${id}"/></animateMotion></circle>`;
    });

    // Director (sin etiqueta duplicada: el título ya está en la cabina)
    svg += `<g class="actor idle_guard" data-actor="director" transform="translate(500,112)" role="button" tabindex="0" aria-label="HomeLab-Director">
      <circle class="actor-glow" cx="0" cy="-30" r="44" fill="${ACCENTS.director}" opacity="0"/>
      <ellipse class="actor-ring" cx="0" cy="-28" rx="31" ry="34" fill="none" stroke="${ACCENTS.director}" stroke-width="1.2" stroke-dasharray="4 6"/>
      ${avatarSvg('director', 'idle_guard', 0.84)}
    </g>`;

    // 8 estaciones: encabezado → burbuja → personaje ilustrado, con huecos claros.
    const STATION_LABELS = {
      infra: 'INFRAESTRUCTURA', secops: 'SEGOPS / DEFENSA', media: 'MULTIMEDIA',
      cloud: 'NUBE & FOTOS', updater: 'UPDATER', backup: 'BACKUP 3-2-1',
      architect: 'ARQUITECTURA', qa: 'QA / AUDITORÍA',
    };
    ORDER.forEach((id, index) => {
      const cx = COLS[index % 4];
      const rowY = ROW_Y[Math.floor(index / 4)];
      const x = cx - STATION_W / 2;
      const name = NAMES[id] || id;
      svg += `<rect class="hq-desk" x="${x}" y="${rowY}" width="${STATION_W}" height="${STATION_H}" rx="14"/>
        <rect class="hq-desk-top" x="${x + 10}" y="${rowY + 8}" width="${STATION_W - 20}" height="4" rx="2"/>
        <text class="hq-station-no" x="${x + 14}" y="${rowY + 25}">${String(index + 1).padStart(2, '0')}</text>
        <text class="hq-sub" x="${x + 40}" y="${rowY + 25}">${STATION_LABELS[id]}</text>
        <g class="actor idle_guard" data-actor="${id}" transform="translate(${cx},${rowY + ACTOR_DY})" role="button" tabindex="0" aria-label="${esc(name)}">
          <circle class="actor-glow" cx="0" cy="-32" r="40" fill="${ACCENTS[id]}" opacity="0"/>
          <ellipse class="actor-ring" cx="0" cy="-30" rx="29" ry="32" fill="none" stroke="${ACCENTS[id]}" stroke-width="1.2" stroke-dasharray="4 6"/>
          ${avatarSvg(id, 'idle_guard', 0.82)}
        </g>`;
    });
    stage.innerHTML = svg + '</svg><div class="office-bubbles" id="office-bubbles"></div>';
    $$('.actor', stage).forEach(actor => {
      const id = actor.dataset.actor;
      actor.addEventListener('click', () => openFicha(id));
      actor.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openFicha(id); }
      });
    });
    stageBuilt = true;
    requestAnimationFrame(positionBubbles);
  }

  /* ---------------- Burbujas ---------------- */
  // Coordenadas del viewBox. La burbuja se ancla ENTRE el título (rowY+25) y la
  // cabeza del personaje (rowY+ACTOR_DY-61): nunca tapa el encabezado ni el avatar.
  function bubbleAnchor(id) {
    if (id === 'director') return { x: 748, y: 62 };
    const index = ORDER.indexOf(id);
    const cx = COLS[index % 4];
    const rowY = ROW_Y[Math.floor(index / 4)];
    return { x: cx, y: rowY + BUBBLE_DY };
  }

  // Coloca cada burbuja en píxeles reales usando la matriz de pantalla del SVG.
  // Así sigue alineada aunque el SVG se escale por `max-height` o por el resize.
  function positionBubbles() {
    const svg = $('#office-stage svg');
    const holder = $('#office-bubbles');
    if (!svg || !holder) return;
    const ctm = svg.getScreenCTM && svg.getScreenCTM();
    if (!ctm) return; // pestaña oculta: se recolocará al activarse
    const stageRect = $('#office-stage').getBoundingClientRect();
    ['director', ...ORDER].forEach(id => {
      const el = holder.querySelector(`[data-bubble="${id}"]`);
      if (!el) return;
      const p = bubbleAnchor(id);
      const pt = svg.createSVGPoint();
      pt.x = p.x; pt.y = p.y;
      const screen = pt.matrixTransform(ctm);
      el.style.left = (screen.x - stageRect.left) + 'px';
      el.style.top = (screen.y - stageRect.top) + 'px';
    });
  }

  function ensureBubbles(data) {
    const holder = $('#office-bubbles');
    if (!holder) return;
    const ids = ['director', ...ORDER];
    if (!holder.childElementCount) {
      holder.innerHTML = ids.map(id => {
        const pos = bubbleAnchor(id);
        // Fallback porcentual antes de que el SVG tenga matriz de pantalla.
        const px = (pos.x / VIEW_W * 100).toFixed(2);
        const py = (pos.y / VIEW_H * 100).toFixed(2);
        return `<div class="office-bubble soft" data-bubble="${id}" style="left:${px}%;top:${py}%"></div>`;
      }).join('');
      requestAnimationFrame(positionBubbles);
    }
    ids.forEach(id => {
      const char = data.characters.find(c => c.id === id);
      const el = holder.querySelector(`[data-bubble="${id}"]`);
      if (!char || !el) return;
      el.textContent = char.bubble_text || '—';
      el.className = `office-bubble ${char.state}`;
      const visible = ['working', 'reporting', 'healing', 'alert'].includes(char.state) || selectedId === id;
      if (!visible) el.classList.add('soft');
      if (selectedId === id) el.classList.add('selected');
    });
  }

  /* ---------------- Actualización de la vista ---------------- */
  function renderStats(data) {
    const s = data.summary || {};
    const backup = s.last_backup;
    const backupOk = backup && backup.ok !== false && backup.integrity === 'verified';
    $('#office-stats').innerHTML = `
      <div class="office-stat">Tareas completadas<strong>${esc(nz(s.completed_tasks, '—'))}</strong></div>
      <div class="office-stat ${backupOk ? '' : 'warn'}">Último backup<strong>${esc((backup || {}).name || 'sin dato')}</strong></div>
      <div class="office-stat ${s.anomalies ? 'bad' : ''}">Sondas Sentinel<strong>${esc(nz(s.probes_ok, '—'))}/${esc(nz(s.probes_total, '—'))}</strong></div>
      <div class="office-stat ${s.corp_online ? '' : 'warn'}">Ledger Corp<strong>${s.corp_online ? 'EN LÍNEA' : 'SIN CONEXIÓN'}</strong></div>`;

    const status = $('#office-status');
    status.classList.toggle('err', !data.ok);
    status.textContent = data.ok
      ? `Sincronizado ${fmtDate(data.generated_at)} · ${(data.active_links || []).length} enlaces activos · ${s.anomalies || 0} anomalías · fuente: corp-tools-api + Sentinel AI`
      : `Sin datos vivos (${esc(data.error || 'fuentes no disponibles')}). Reintentando en 4 s…`;

    $('#office-legend').innerHTML = Object.entries(STATES).map(([key, info]) =>
      `<span class="legend-chip"><i style="background:${info.color}"></i>${info.label}</span>`
    ).join('') + `<span class="legend-chip">⬡ partículas = flujo de datos real</span>`;
  }

  function updateStage(data) {
    const stage = $('#office-stage');
    if (!stage) return;
    data.characters.forEach(char => {
      const actor = stage.querySelector(`[data-actor="${char.id}"]`);
      const status = stage.querySelector(`[data-status="${char.id}"]`);
      if (actor) {
        actor.setAttribute('class', `actor ${char.state}${selectedId === char.id ? ' selected' : ''}`);
        actor.setAttribute('aria-label', `${char.name}: ${char.bubble_text}`);
      }
      if (status) {
        const info = STATES[char.state] || STATES.idle_guard;
        status.textContent = `${info.icon} ${info.label}`;
        status.setAttribute('fill', info.color);
        if (char.id === 'director') {
          const dot = stage.querySelector('#director-dot');
          if (dot) dot.setAttribute('fill', info.color);
        }
      }
    });
    const hot = new Set((data.active_links || []).filter(l => l.from === 'director').map(l => l.to));
    ORDER.forEach(id => {
      const path = stage.querySelector(`#link-${id}`);
      if (!path) return;
      const charState = data.characters.find(c => c.id === id);
      const isHot = hot.has(id) || (charState && ['healing', 'alert'].includes(charState.state));
      path.setAttribute('class', `hq-data-line${isHot ? ' hot' : ''}`);
      $$(`#link-${id} ~ .hq-particle`, stage).forEach(p => p.classList.toggle('hot', isHot));
    });
    ensureBubbles(data);
  }

  function renderCards(data) {
    const holder = $('#office-cards');
    if (!holder) return;
    holder.innerHTML = data.characters.map(char => {
      const info = STATES[char.state] || STATES.idle_guard;
      return `<article class="office-card glass ${selectedId === char.id ? 'selected' : ''}" data-card="${char.id}" role="button" tabindex="0">
        ${miniAvatar(char.id, char.state)}
        <div class="office-card-body">
          <div class="office-card-top">
            <div><div class="office-card-name">${esc(char.name)}</div><div class="office-card-role">${esc(char.role)}</div></div>
            <span class="state-badge" style="color:${info.color};background:${info.color}1f">${info.icon} ${info.label}</span>
          </div>
          <div class="office-card-bubble">${esc(char.bubble_text || '—')}</div>
          <div class="office-card-foot">
            <button type="button" data-ficha="${char.id}">Ver ficha</button>
            <span>QA ${Number(char.qa_score || 0).toFixed(2)} · ${esc(char.report_count || 0)} informes</span>
          </div>
        </div>
      </article>`;
    }).join('');
    $$('[data-card]', holder).forEach(card => {
      card.addEventListener('click', (e) => {
        if (e.target.closest('[data-ficha]')) return;
        openFicha(card.dataset.card);
      });
    });
    $$('[data-ficha]', holder).forEach(btn =>
      btn.addEventListener('click', () => openFicha(btn.dataset.ficha))
    );
  }

  function render(data) {
    lastData = data;
    buildStage();
    updateStage(data);
    renderStats(data);
    renderCards(data);
    requestAnimationFrame(positionBubbles);
  }

  /* ---------------- Ficha interactiva ---------------- */
  function openFicha(id) {
    const char = ((lastData || {}).characters || []).find(c => c.id === id);
    if (!char) { notify('Datos del personaje no disponibles'); return; }
    selectedId = id;
    if (lastData) { updateStage(lastData); renderCards(lastData); }
    const info = STATES[char.state] || STATES.idle_guard;
    const links = (char.active_links || []).map(l =>
      `<span class="ficha-link">${esc(l.from)} → ${esc(l.to)} · ${esc(l.kind)}</span>`
    ).join('') || '<span class="ficha-link">sin enlaces activos</span>';

    $('#modal-title').textContent = `${char.station} · ${char.name}`;
    $('#modal-body').innerHTML = `<div class="ficha">
      <div class="ficha-hero">
        ${miniAvatar(char.id, char.state)}
        <div>
          <h4>${esc(char.name)} <span class="state-badge" style="color:${info.color};background:${info.color}1f">${info.icon} ${info.label}</span></h4>
          <p>${esc(char.role)} · ${esc(char.agent)}</p>
        </div>
      </div>
      <div class="ficha-metrics">
        <div class="ficha-metric"><span>Puntuación QA</span><strong>${Number(char.qa_score || 0).toFixed(3)}</strong></div>
        <div class="ficha-metric"><span>Informes en ledger</span><strong>${esc(nz(char.report_count, 0))}</strong></div>
        <div class="ficha-metric"><span>Última_activity</span><strong style="font-size:11px">${esc(fmtDate(char.last_seen))}</strong></div>
      </div>
      <div class="ficha-section"><h5>Biografía operativa</h5><p>${esc(char.bio || '—')}</p></div>
      <div class="ficha-section"><h5>Actividad en vivo</h5><p>${esc(char.bubble_text || '—')}</p></div>
      <div class="ficha-section"><h5>Último informe del ledger</h5><pre>${esc(char.last_report || 'Sin informe registrado todavía.')}</pre></div>
      <div class="ficha-section"><h5>Enlaces activos</h5><div class="ficha-links">${links}</div></div>
      <div id="ficha-feedback" class="ficha-feedback hidden" aria-live="polite"></div>
      <div class="ficha-actions">
        <button class="btn btn-secondary" id="ficha-close">Cerrar</button>
        <button class="btn btn-primary" id="ficha-assign">Asignar revisión ahora</button>
      </div>
    </div>`;
    $('#modal-backdrop').classList.remove('hidden');
    $('#ficha-close').addEventListener('click', () => closeFicha());
    $('#ficha-assign').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true; btn.textContent = 'Asignando…';
      const result = await command('assign_review', char.id, false);
      btn.disabled = false; btn.textContent = 'Asignar revisión ahora';
      const fb = $('#ficha-feedback');
      if (!fb) return;
      fb.classList.remove('hidden');
      fb.classList.toggle('err', !result.ok);
      fb.textContent = result.ok
        ? `✔ Revisión encolada para ${char.name} (HTTP ${nz(result.status_code, 200)}). Verás la animación al actualizarse el ledger.`
        : `✖ No se pudo asignar: ${result.error || result.response || result.status_code || 'error desconocido'}`;
      notify(result.ok ? `Revisión asignada a ${char.name}` : 'No se pudo asignar la revisión');
      setTimeout(refresh, 1400);
    });
  }

  function clearSelection() {
    selectedId = null;
    if (lastData) { updateStage(lastData); renderCards(lastData); }
  }

  function closeFicha() {
    clearSelection();
    $('#modal-backdrop').classList.add('hidden');
  }

  const modalClose = $('#modal-close');
  if (modalClose) modalClose.addEventListener('click', clearSelection);
  const modalBackdrop = $('#modal-backdrop');
  if (modalBackdrop) modalBackdrop.addEventListener('click', (e) => {
    if (e.target.id === 'modal-backdrop') clearSelection();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && selectedId) clearSelection();
  });

  /* ---------------- Comandos ---------------- */
  async function command(action, employee = null, confirmFirst = true) {
    if (confirmFirst && action === 'director_round') {
      const ok = confirm('¿Convocar una ronda general completa del Director? Los 8 empleados generarán informes reales y se enviará el reporte a Telegram.');
      if (!ok) return { ok: false, cancelled: true };
    }
    try {
      const res = await fetch('/api/corp-office-command', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, employee }),
      });
      return await res.json();
    } catch (err) {
      return { ok: false, error: err.message };
    }
  }

  const conveneBtn = $('#office-convene');
  if (conveneBtn) conveneBtn.addEventListener('click', async () => {
    conveneBtn.disabled = true;
    const original = conveneBtn.textContent;
    conveneBtn.textContent = '⚡ Despachando órdenes…';
    const result = await command('director_round', null, true);
    conveneBtn.disabled = false;
    conveneBtn.textContent = original;
    if (result.cancelled) return;
    if (result.ok) {
      notify(`Ronda general iniciada · ${result.target} · HTTP ${nz(result.status_code, 200)}`);
      setTimeout(refresh, 1800);
    } else {
      notify(`No se pudo convocar: ${result.error || result.response || result.status_code || 'error'}`);
    }
  });

  /* ---------------- Polling 4 s ---------------- */
  async function refresh() {
    if (inFlight) return;
    inFlight = true;
    try {
      const res = await fetch('/api/corp-office-state', { credentials: 'same-origin' });
      if (res.status === 401) {
        $('#office-status').textContent = 'Sesión expirada · inicie sesión de nuevo.';
        stopOffice();
        return;
      }
      const data = await res.json();
      render(data);
    } catch (err) {
      const status = $('#office-status');
      if (status) {
        status.classList.add('err');
        status.textContent = `Sin conexión con el puente Corp: ${err.message}. Reintentando en 4 s…`;
      }
    } finally {
      inFlight = false;
    }
  }

  function startOffice() {
    if (running) return;
    running = true;
    refresh();
    clearInterval(timer);
    timer = setInterval(refresh, 4000);
  }

  function stopOffice() {
    running = false;
    clearInterval(timer);
    timer = null;
  }

  $$('[data-tab]').forEach(btn => btn.addEventListener('click', () => {
    if (btn.dataset.tab === 'office') {
      startOffice();
      requestAnimationFrame(positionBubbles);
    } else stopOffice();
  }));

  let resizeRaf = 0;
  window.addEventListener('resize', () => {
    cancelAnimationFrame(resizeRaf);
    resizeRaf = requestAnimationFrame(positionBubbles);
  });

  // Si la pestaña ya viene activa (restauración de sesión / PWA).
  const officeTab = $('#tab-office');
  if (officeTab && officeTab.classList.contains('active')) startOffice();
})();
