const $ = (s) => document.querySelector(s);
let lastState = null;

function fmtTs(ts){ if(!ts) return '—'; const d=new Date(ts*1000); return d.toLocaleString('es-DO',{hour12:false}); }
function esc(s){ return (s??'').toString().replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }

async function fetchState(){
  try{
    const r = await fetch('/api/state'); if(!r.ok) throw new Error(r.status);
    lastState = await r.json();
    render(lastState);
  }catch(e){ $('#cycleInfo').textContent = 'Error obteniendo estado: '+e.message; }
}

function nodeStatusClass(node){
  if(!node) return 'warn';
  if(node.up === false) return 'bad';
  if(node.ssd && node.ssd.critical_95) return 'bad';
  if(node.ssd && node.ssd.alert_92) return 'warn';
  return 'ok';
}

function renderMap(state){
  const twin = state.twin || {}; const nodes = twin.nodes || {};
  const host = nodes.host || {}; const zte = nodes.gateway_zte || {}; const ap = nodes.ap_nexxt || {}; const tv = nodes.tcl_tv || {};
  const docker = twin.docker || {}; const running = docker.running ?? '?'; const total = docker.count ?? '?';
  // aggregate container health
  let contClass = 'ok';
  for(const c of (docker.containers||[])){
    if(c.health==='unhealthy' || c.restarting || c.status==='restarting' || c.status==='exited') { contClass = 'warn'; }
  }
  const contNode = {name:`Contenedores (${running}/${total})`, up: contClass!=='bad'};
  const map = $('#map');
  map.innerHTML = `
    <div class="map-row">
      ${nodeHtml('zte','Gateway ZTE', zte.ip||'192.168.1.1', zte)}
      <div class="arrow">→</div>
      ${nodeHtml('ap','AP Nexxt', ap.ip||'192.168.1.254', ap)}
      <div class="arrow">→</div>
      ${nodeHtml('host','OptiPlex Host', host.ip||'192.168.1.3', host)}
    </div>
    <div class="arrow">↓</div>
    <div class="map-row">
      ${nodeHtml('containers','Contenedores Docker', `${running}/${total} activos`, {up: contClass!=='bad', _class: contClass, containers: docker.containers||[]})}
      <div class="arrow">→</div>
      ${nodeHtml('tv','TCL Android TV 12', tv.ip||'192.168.1.8', tv)}
    </div>`;
  map.querySelectorAll('.node').forEach(el=>{
    el.addEventListener('click', ()=>{
      const key = el.dataset.key; showNodeDetail(key, state);
    });
  });
}

function nodeHtml(key, title, sub, node){
  let cls = nodeStatusClass(node);
  if(node && node._class) cls = node._class;
  return `<div class="node ${cls}" data-key="${key}"><div class="n-title">${esc(title)}</div><div class="n-sub">${esc(sub)}</div></div>`;
}

function showNodeDetail(key, state){
  const twin = state.twin || {}; const nodes = twin.nodes || {}; const probes = state.probes || {};
  let obj = null; let label = key;
  if(key==='zte'){ obj = nodes.gateway_zte; label='Gateway ZTE (192.168.1.1)'; }
  else if(key==='ap'){ obj = nodes.ap_nexxt; label='AP Nexxt (192.168.1.254)'; }
  else if(key==='host'){ obj = nodes.host; label='Host OptiPlex (192.168.1.3)'; }
  else if(key==='tv'){ obj = nodes.tcl_tv; label='TCL Android TV 12 (192.168.1.8)'; }
  else if(key==='containers'){ obj = {summary: {count: (twin.docker||{}).count, running: (twin.docker||{}).running}, containers: (twin.docker||{}).containers||[]}; label='Contenedores Docker'; }
  $('#nodeDetail').textContent = label + "\n" + JSON.stringify(obj, null, 2);
}

function renderHost(state){
  const twin = state.twin || {}; const host = (twin.nodes||{}).host || {}; const ssd = host.ssd || {};
  const pct = ssd.used_pct ?? 0; const bar = $('#ssdBar');
  bar.style.width = Math.max(0,Math.min(100,pct)) + '%';
  bar.className = 'bar' + (pct>=95?' bad': pct>=92?' warn':'');
  $('#ssdPctLabel').textContent = pct? `— ${pct}%` : '';
  $('#ssdMeta').textContent = ssd.total_gb ? `${ssd.used_gb} / ${ssd.total_gb} GB usados · ${ssd.free_gb} GB libres` : (ssd.error||'—');
  const ram = host.ram || {}; const qs = host.quicksync || {}; const ts = host.tailscale || {};
  const stats = [
    ['CPU Temp', host.cpu_temp_c!=null? host.cpu_temp_c+' °C':'—'],
    ['RAM', ram.used_pct!=null? `${ram.used_pct}% (${ram.used_mb}/${ram.total_mb} MB)`:'—'],
    ['QuickSync', qs.present? 'OK (/dev/dri/renderD128)': 'No detectado'],
    ['Tailscale', ts.present? 'Interfaz presente':'No detectada'],
  ];
  $('#hostStats').innerHTML = stats.map(s=>`<div class="stat"><div class="k">${esc(s[0])}</div><div class="v">${esc(s[1])}</div></div>`).join('');
}

function badge(st){
  const s=(st||'unknown').toLower();
  let cls='ok'; if(['degraded','warning'].includes(s)) cls='degraded'; if(['down','error','critical'].includes(s)) cls='down';
  return `<span class="badge ${cls}">${esc(s)}</span>`;
}

function renderProbes(state){
  const probes = state.probes || {};
  const items = [];
  const order = ['threadfin','pihole','nextcloud','immich','jellyfin'];
  for(const k of order){
    const p = probes[k]; if(!p) continue;
    let extra = '';
    if(k==='threadfin') extra = ` · ${p.channels??0} canales${p.channels_dropped?' (caída detectada)':''}${p.sync_failed?' · sync falló':''}`;
    if(k==='pihole') extra = ` · DNS ${p.resolve_ok?'OK':'Fallo'} (${p.resolve_latency_ms??'?'} ms) · filtrado ${p.filtering_active?'activo':'?'}`;
    if(k==='immich') extra = ` · /nextcloud-photos:ro ${p.mount_nextcloud_photos_ro===true?'OK':p.mount_nextcloud_photos_ro===false?'NO OK':'?'}`;
    items.push(`<div class="probe"><div><strong>${esc(p.name||k)}</strong><span class="muted small">${esc(extra)}</span></div>${badge(p.status)}</div>`);
  }
  const disk = probes.disk || {};
  if(disk.used_pct!=null) items.push(`<div class="probe"><div><strong>Disco /dev/sda1</strong><span class="muted small"> · ${disk.used_pct}% usado · ${disk.free_gb} GB libres</span></div>${badge(disk.critical_95?'critical':disk.warn_92?'warning':'ok')}</div>`);
  const cl = probes.containers_logs || {}; const issues = (cl.issues||[]).length;
  items.push(`<div class="probe"><div><strong>Contenedores/Logs</strong><span class="muted small"> · ${issues} incidencia(s) reciente(s)</span></div>${badge(issues? 'warning':'ok')}</div>`);
  $('#probes').innerHTML = items.join('') || '<div class="muted">Sin datos aún.</div>';
}

function renderRepairs(state){
  const repairs = state.repairs || []; const anomalies = state.anomalies || [];
  const items = [];
  if(anomalies.length){
    items.push(`<div class="feed-item"><div class="meta">Estado actual · ${anomalies.length} anomalía(s) detectada(s) en el último ciclo</div>${anomalies.map(a=>`<div>• <strong>${esc(a.component)}</strong> [${esc(a.severity)}] — ${esc(a.summary||'')}</div>`).join('')}</div>`);
  }
  for(const r of repairs){
    const meta = `${fmtTs(r.ts)} · ${esc(r.component)} · tier ${r.tier} · ${esc(r.status)} · ${esc(r.action)}`;
    let body = '';
    try{ if(r.diagnosis) body += `<div style="margin-top:4px">${esc(r.diagnosis).slice(0,500)}</div>`; }catch(e){}
    items.push(`<div class="feed-item"><div class="meta">${meta}</div>${body}</div>`);
  }
  $('#repairs').innerHTML = items.join('') || '<div class="muted">Sin eventos registrados todavía.</div>';
}

function renderContainers(state){
  const docker = (state.twin||{}).docker || {}; const list = docker.containers || [];
  $('#containers').innerHTML = list.map(c=>{
    const st = c.health ? `${c.status} / ${c.health}` : c.status;
    const cls = (c.health==='unhealthy'||c.restarting||c.status==='restarting')?'down':(c.running?'ok':'warning');
    const stats = c.stats ? ` · CPU ${c.stats.cpu_pct??'?'}% · RAM ${c.stats.mem_used_mb??'?'} MB` : '';    return `<div class="ct"><div class="name">${esc(c.name)} ${badge(cls)}</div><div class="sub">${esc(st)} · ${esc((c.image||'').split('@')[0])}${esc(stats)}</div><div class="sub">${esc((c.networks||[]).join(', '))}</div></div>`;
  }).join('') || '<div class="muted">Sin datos de contenedores.</div>';
}

function render(state){
  $('#cycleInfo').textContent = 'Último ciclo: ' + fmtTs(state.last_cycle);
  renderMap(state); renderHost(state); renderProbes(state); renderRepairs(state); renderContainers(state);
}

async function runAudit(){
  const btn = $('#auditBtn'); btn.disabled = true; btn.textContent = 'Auditando...';
  $('#auditModal').classList.remove('hidden');
  $('#auditResult').textContent = 'Ejecutando auditoría completa y sondas L7...';
  try{
    const r = await fetch('/api/audit', {method:'POST'});
    const data = await r.json();
    $('#auditResult').textContent = JSON.stringify(data.audit, null, 2);
    await fetchState();
  }catch(e){ $('#auditResult').textContent = 'Error: '+e.message; }
  finally{ btn.disabled=false; btn.textContent='Auditar Todo con IA Ahora'; }
}

$('#auditBtn').addEventListener('click', runAudit);
$('#closeAudit').addEventListener('click', ()=>$('#auditModal').classList.add('hidden'));

$('#askForm').addEventListener('submit', async (e)=>{
  e.preventDefault();
  const input = $('#askInput'); const q = input.value.trim(); if(!q) return;
  const log = $('#chatLog');
  log.insertAdjacentHTML('beforeend', `<div class="msg user">${esc(q)}</div>`);
  input.value=''; log.scrollTop = log.scrollHeight;
  try{
    const r = await fetch('/api/ask', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({question:q})});
    const data = await r.json();
    log.insertAdjacentHTML('beforeend', `<div class="msg bot">${esc(data.answer||'(sin respuesta)')}</div>`);
  }catch(err){ log.insertAdjacentHTML('beforeend', `<div class="msg bot">Error: ${esc(err.message)}</div>`); }
  log.scrollTop = log.scrollHeight;
});

fetchState();
setInterval(fetchState, 10000);
