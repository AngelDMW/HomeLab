const $ = (s) => document.querySelector(s);
let lastState = null;
let auditInProgress = false;

function fmtTs(ts){ if(!ts) return "—"; const d=new Date(ts*1000); return d.toLocaleString("es-DO",{hour12:false}); }
function esc(s){ return (s??"").toString().replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }

// Safe lightweight markdown: headings, bold, inline/fenced code, bullet lists
function renderMarkdown(text){
  if(text == null) return "";
  let s = esc(text).replace(/\r\n/g, "\n");
  // fenced code blocks
  s = s.replace(/```([\s\S]*?)```/g, (_, code) => `<pre><code>${code.replace(/^\n|\n$/g,"")}</code></pre>`);
  // headings
  s = s.replace(/^###\s+(.*)$/gm, "<h4>$1</h4>");
  s = s.replace(/^##\s+(.*)$/gm, "<h3>$1</h3>");
  s = s.replace(/^#\s+(.*)$/gm, "<h2>$1</h2>");
  // inline code and bold
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  // lists
  const lines = s.split("\n");
  let out = [], inList = false;
  for(const line of lines){
    const m = line.match(/^\s*[-*]\s+(.*)$/);
    if(m){
      if(!inList){ out.push("<ul>"); inList = true; }
      out.push(`<li>${m[1]}</li>`);
      continue;
    }
    if(inList){ out.push("</ul>"); inList = false; }
    if(/^<h\d|^<pre>|^<ul>|^<\/ul>|^<li>/.test(line)){
      out.push(line);
    } else if(line.trim()===""){
      out.push("<br>");
    } else {
      out.push(`<div>${line}</div>`);
    }
  }
  if(inList) out.push("</ul>");
  return out.join("\n").replace(/(<br>\s*){3,}/g,"<br><br>");
}

async function fetchState(){
  try{
    const r = await fetch("/api/state"); if(!r.ok) throw new Error(r.status);
    lastState = await r.json();
    render(lastState);
  }catch(e){
    if(auditInProgress) return; // ignore transient polling errors during manual audit
    $("#cycleInfo").textContent = "Error obteniendo estado: "+e.message;
  }
}

function nodeStatusClass(node){
  if(!node) return "warn";
  if(node.up === false) return "bad";
  if(node.ssd && node.ssd.critical_95) return "bad";
  if(node.ssd && node.ssd.alert_92) return "warn";
  return "ok";
}

function renderMap(state){
  const twin = state.twin || {}; const nodes = twin.nodes || {};
  const host = nodes.host || {}; const zte = nodes.gateway_zte || {}; const ap = nodes.ap_nexxt || {}; const tv = nodes.tcl_tv || {};
  const node2 = nodes.node2 || {};
  const docker = twin.docker || {}; const running = docker.running ?? "?"; const total = docker.count ?? "?";
  // aggregate container health
  let contClass = "ok";
  for(const c of (docker.containers||[])){
    if(c.health==="unhealthy" || c.restarting || c.status==="restarting" || c.status==="exited") { contClass = "warn"; }
  }
  const node2Sub = `${node2.ip||"192.168.1.4"} · 24/7 crítico${node2.up===false?" · CAÍDO":""}`;
  const map = $("#map");
  map.innerHTML = `
    <div class="map-row">
      ${nodeHtml("zte","Gateway ZTE", zte.ip||"192.168.1.1", zte)}
      <div class="arrow">→</div>
      ${nodeHtml("ap","AP Nexxt", ap.ip||"192.168.1.254", ap)}
    </div>
    <div class="arrow">↓ LAN</div>
    <div class="map-row">
      ${nodeHtml("host","OptiPlex Host (Nodo 1)", host.ip||"192.168.1.3", host)}
      ${nodeHtml("node2","homelab-node2 (Nodo 2)", node2Sub, node2)}
      ${nodeHtml("tv","TCL Android TV 12", tv.ip||"192.168.1.8", tv)}
    </div>
    <div class="arrow">↓</div>
    <div class="map-row">
      ${nodeHtml("containers","Contenedores Docker", `${running}/${total} activos`, {up: contClass!=="bad", _class: contClass, containers: docker.containers||[]})}
    </div>`;
  map.querySelectorAll(".node").forEach(el=>{
    el.addEventListener("click", ()=>{
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
  const twin = state.twin || {}; const nodes = twin.nodes || {};
  let obj = null; let label = key; let extraHtml = "";
  if(key==="zte"){ obj = nodes.gateway_zte; label="Gateway ZTE (192.168.1.1)"; }
  else if(key==="ap"){ obj = nodes.ap_nexxt; label="AP Nexxt (192.168.1.254)"; }
  else if(key==="host"){ obj = nodes.host; label="Host OptiPlex (192.168.1.3)"; }
  else if(key==="node2"){
    obj = nodes.node2; label="homelab-node2 (Nodo 2, 192.168.1.4) — crítico 24/7";
    const mac = (obj && obj.mac) ? esc(obj.mac) : "—";
    extraHtml = `<div style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="muted small">MAC: ${mac}</span><button id="wolBtn" class="btn small">Wake-on-LAN (recuperación manual)</button><span id="wolMsg" class="muted small"></span></div>`;
  }
  else if(key==="tv"){ obj = nodes.tcl_tv; label="TCL Android TV 12 (192.168.1.8)"; }
  else if(key==="containers"){ obj = {summary: {count: (twin.docker||{}).count, running: (twin.docker||{}).running}, containers: (twin.docker||{}).containers||[]}; label="Contenedores Docker"; }
  const detail = $("#nodeDetail");
  detail.innerHTML = `<div><strong>${esc(label)}</strong></div>${extraHtml}<pre style="margin:8px 0 0;white-space:pre-wrap">${esc(JSON.stringify(obj, null, 2))}</pre>`;
  const wolBtn = document.getElementById("wolBtn");
  if(wolBtn){
    wolBtn.addEventListener("click", async ()=>{
      wolBtn.disabled = true; const msg = document.getElementById("wolMsg"); msg.textContent = "Enviando WoL...";
      try{
        const r = await fetch("/api/node2/wol", {method:"POST"});
        const d = await r.json();
        msg.textContent = d.ok ? ("OK: "+d.detail) : ("Error: "+d.detail);
      }catch(e){ msg.textContent = "Error: "+e.message; }
      finally{ wolBtn.disabled = false; }
    });
  }
}

function renderHost(state){
  const twin = state.twin || {}; const host = (twin.nodes||{}).host || {}; const ssd = host.ssd || {};
  const pct = ssd.used_pct ?? 0; const bar = $("#ssdBar");
  bar.style.width = Math.max(0,Math.min(100,pct)) + "%";
  bar.className = "bar" + (pct>=95?" bad": pct>=92?" warn":"");
  $("#ssdPctLabel").textContent = pct? `— ${pct}%` : "";
  $("#ssdMeta").textContent = ssd.total_gb ? `${ssd.used_gb} / ${ssd.total_gb} GB usados · ${ssd.free_gb} GB libres` : (ssd.error||"—");
  const ram = host.ram || {}; const qs = host.quicksync || {}; const ts = host.tailscale || {};
  const stats = [
    ["CPU Temp", host.cpu_temp_c!=null? host.cpu_temp_c+" °C":"—"],
    ["RAM", ram.used_pct!=null? `${ram.used_pct}% (${ram.used_mb}/${ram.total_mb} MB)`:"—"],
    ["QuickSync", qs.present? "OK (/dev/dri/renderD128)": "No detectado"],
    ["Tailscale", ts.present? "Interfaz presente":"No detectada"],
  ];
  $("#hostStats").innerHTML = stats.map(s=>`<div class="stat"><div class="k">${esc(s[0])}</div><div class="v">${esc(s[1])}</div></div>`).join("");
}

function badge(st){
  const s = String(st || "unknown").toLowerCase();
  let cls="ok"; if(["degraded","warning"].includes(s)) cls="degraded"; if(["down","error","critical"].includes(s)) cls="down";
  return `<span class="badge ${cls}">${esc(s)}</span>`;
}

function probeLabel(k,p){
  const names = {threadfin:"Threadfin", pihole:"Pi-hole", nextcloud:"Nextcloud", immich:"Immich", jellyfin:"Jellyfin", disk:"Disco /dev/sda1", containers_logs:"Contenedores/Logs", node2_portainer:"Nodo 2 Portainer Agent", node2_immich_ml:"Nodo 2 Immich ML", node2_metrics:"Nodo 2 (métricas)", nfs_node2_storage:"NFS /mnt/node2-storage"};
  return names[k] || (p && p.name) || k;
}

function renderProbes(state){
  const probes = state.probes || {};
  const items = [];
  const order = ["threadfin","pihole","nextcloud","immich","jellyfin"];
  for(const k of order){
    const p = probes[k]; if(!p) continue;
    let extra = "";
    if(k==="threadfin") extra = ` · ${p.channels??0} canales${p.channels_dropped?" (caída detectada)":""}${p.sync_failed?" · sync falló":""}`;
    if(k==="pihole") extra = ` · DNS ${p.resolve_ok?"OK":"Fallo"} (${p.resolve_latency_ms??"?"} ms) · filtrado ${p.filtering_active?"activo":"?"}`;
    if(k==="immich") extra = ` · /nextcloud-photos:ro ${p.mount_nextcloud_photos_ro===true?"OK":p.mount_nextcloud_photos_ro===false?"NO OK":"?"}`;
    items.push(`<div class="probe"><div><strong>${esc(p.name||k)}</strong><span class="muted small">${esc(extra)}</span></div>${badge(p.status)}</div>`);
  }
  const disk = probes.disk || {};
  if(disk.used_pct!=null) items.push(`<div class="probe"><div><strong>Disco /dev/sda1</strong><span class="muted small"> · ${disk.used_pct}% usado · ${disk.free_gb} GB libres</span></div>${badge(disk.critical_95?"critical":disk.warn_92?"warning":"ok")}</div>`);
  // Node 2
  const n2p = probes.node2_portainer || {};
  if(n2p.name) items.push(`<div class="probe"><div><strong>${esc(n2p.name)}</strong><span class="muted small"> · ${esc(n2p.host||"")} :${n2p.port||""} ${n2p.status_code? "(HTTP "+n2p.status_code+")":""}</span></div>${badge(n2p.status)}</div>`);
  const n2ml = probes.node2_immich_ml || {};
  if(n2ml.name) items.push(`<div class="probe"><div><strong>${esc(n2ml.name)}</strong><span class="muted small"> · ${esc(n2ml.endpoint||"/ping")} ${n2ml.response?("· "+String(n2ml.response).slice(0,20)) : ""}</span></div>${badge(n2ml.status)}</div>`);
  const n2m = probes.node2_metrics || {};
  if(n2m.name){
    const ram = n2m.ram||{}; const st = n2m.storage||{};
    let extra = ` · ${n2m.online?"online":"offline"}`;
    if(ram.used_pct!=null) extra += ` · RAM ${ram.used_pct}% (${ram.used_mb}/${ram.total_mb} MB)${ram.alert_85?" ⚠>85%":""}`;
    if(st.free_gb!=null) extra += ` · /mnt/storage libre ${st.free_gb} GB (${st.used_pct}% usado)`;
    if(n2m.ssh_ok===false && n2m.online) extra += " · SSH métricas no disponible";
    items.push(`<div class="probe"><div><strong>${esc(n2m.name)}</strong><span class="muted small">${esc(extra)}</span></div>${badge(n2m.status)}</div>`);
  }
  const nfs = probes.nfs_node2_storage || {};
  if(nfs.name) items.push(`<div class="probe"><div><strong>${esc(nfs.name)}</strong><span class="muted small"> · ${esc(nfs.path||"")} ${nfs.mounted?"montado":"no montado"} ${nfs.accessible?"· accesible":""}</span></div>${badge(nfs.status)}</div>`);
  const cl = probes.containers_logs || {}; const issues = (cl.issues||[]).length;
  items.push(`<div class="probe"><div><strong>Contenedores/Logs</strong><span class="muted small"> · ${issues} incidencia(s) reciente(s)</span></div>${badge(issues? "warning":"ok")}</div>`);
  $("#probes").innerHTML = items.join("") || '<div class="muted">Sin datos aún.</div>';
}

function renderRepairs(state){
  const repairs = state.repairs || []; const anomalies = state.anomalies || [];
  const items = [];
  if(anomalies.length){
    items.push(`<div class="feed-item"><div class="meta">Estado actual · ${anomalies.length} anomalía(s) detectada(s) en el último ciclo</div>${anomalies.map(a=>`<div>• <strong>${esc(a.component)}</strong> [${esc(a.severity)}] — ${esc(a.summary||"")}</div>`).join("")}</div>`);
  }
  for(const r of repairs){
    const meta = `${fmtTs(r.ts)} · ${esc(r.component)} · tier ${r.tier} · ${esc(r.status)} · ${esc(r.action)}`;
    let body = "";
    try{ if(r.diagnosis) body += `<div style="margin-top:4px">${esc(String(r.diagnosis)).slice(0,500)}</div>`; }catch(e){}
    items.push(`<div class="feed-item"><div class="meta">${meta}</div>${body}</div>`);
  }
  $("#repairs").innerHTML = items.join("") || '<div class="muted">Sin eventos registrados todavía.</div>';
}

function renderContainers(state){
  const docker = (state.twin||{}).docker || {}; const list = docker.containers || [];
  $("#containers").innerHTML = list.map(c=>{
    const st = c.health ? `${c.status} / ${c.health}` : c.status;
    const cls = (c.health==="unhealthy"||c.restarting||c.status==="restarting")?"down":(c.running?"ok":"warning");
    const stats = c.stats ? ` · CPU ${c.stats.cpu_pct??"?"}% · RAM ${c.stats.mem_used_mb??"?"} MB` : "";
    return `<div class="ct"><div class="name">${esc(c.name)} ${badge(cls)}</div><div class="sub">${esc(st)} · ${esc((c.image||"").split("@")[0])}${esc(stats)}</div><div class="sub">${esc((c.networks||[]).join(", "))}</div></div>`;
  }).join("") || '<div class="muted">Sin datos de contenedores.</div>';
}

function render(state){
  $("#cycleInfo").textContent = "Último ciclo: " + fmtTs(state.last_cycle);
  renderMap(state); renderHost(state); renderProbes(state); renderRepairs(state); renderContainers(state);
}

function buildAuditReport(audit){
  const twin = audit.twin || {}; const probes = audit.probes || {}; const nodes = twin.nodes || {};
  const host = nodes.host || {}; const node2 = nodes.node2 || {}; const anomalies = audit.anomalies || [];
  const probeKeys = ["threadfin","pihole","nextcloud","immich","jellyfin","disk","containers_logs","node2_portainer","node2_immich_ml"]; // 9 sondas principales
  const overallOk = anomalies.filter(a=>a.severity==="critical").length===0;
  function probeRow(k){
    const p = probes[k] || {}; const name = probeLabel(k,p);
    let st = p.status || (p.ok===true?"ok":p.ok===false?"down":"unknown");
    if(k==="containers_logs"){ const issues=(p.issues||[]).length; st = issues? "warning":"ok"; return `<tr><td>${esc(name)}</td><td>${badge(st)}</td><td class="muted small">${issues} incidencia(s)</td></tr>`; }
    let detail = "";
    if(k==="threadfin") detail = `${p.channels??0} canales, M3U ${p.m3u_ok?"OK":"Fallo"}, XML ${p.xml_ok?"OK":"Fallo"}`;
    else if(k==="pihole") detail = `DNS ${p.resolve_ok?"OK":"Fallo"} ${p.resolve_latency_ms!=null? "("+p.resolve_latency_ms+" ms)":""}, filtrado ${p.filtering_active?"activo":"?"}`;
    else if(k==="nextcloud") detail = p.details && p.details.versionstring ? `v${p.details.versionstring}` : (p.endpoint||"");
    else if(k==="immich") detail = `/nextcloud-photos:ro ${p.mount_nextcloud_photos_ro===true?"OK":p.mount_nextcloud_photos_ro===false?"NO OK":"?"}`;
    else if(k==="jellyfin") detail = p.details && p.details.Version ? `v${p.details.Version}` : "";
    else if(k==="disk") detail = p.used_pct!=null ? `${p.used_pct}% usado · ${p.free_gb} GB libres` : "";
    else if(k==="node2_portainer") detail = `TCP/TLS ${p.ok?"OK":"Fallo"} ${p.status_code? "(HTTP "+p.status_code+")":""}`;
    else if(k==="node2_immich_ml") detail = `${p.endpoint||"/ping"} ${p.response? "→ "+String(p.response).slice(0,30):""}`;
    return `<tr><td>${esc(name)}</td><td>${badge(st)}</td><td class="muted small">${esc(detail)}</td></tr>`;
  }
  const nfs = probes.nfs_node2_storage || {};
  const ssd = host.ssd || {}; const ram = host.ram || {};
  const n2ram = ((node2.metrics||{}).ram)||{}; const n2st = ((node2.metrics||{}).storage)||{};
  const node1Html = `<div class="feed-item"><strong>Nodo 1 — OptiPlex (192.168.1.3)</strong> ${badge(host.up===false?"down":(ssd.critical_95?"critical":ssd.alert_92?"warning":"ok"))}
    <div class="muted small" style="margin-top:4px">CPU ${host.cpu_temp_c!=null?host.cpu_temp_c+"°C":"—"} · RAM ${ram.used_pct!=null?ram.used_pct+"%":""} · SSD ${ssd.used_pct!=null?ssd.used_pct+"% usado ("+ssd.free_gb+" GB libres)":"—"} · Contenedores ${(twin.docker||{}).running}/${(twin.docker||{}).count}</div></div>`;
  const node2Html = `<div class="feed-item"><strong>Nodo 2 — homelab-node2 (192.168.1.4)</strong> ${badge(node2.up===false?"down":"ok")}
    <div class="muted small" style="margin-top:4px">${node2.up===false?"Nodo crítico 24/7 caído":`RAM ${n2ram.used_pct!=null?n2ram.used_pct+"%":""} · /mnt/storage libre ${n2st.free_gb!=null?n2st.free_gb+" GB ("+n2st.used_pct+"% usado)":"—"} · Portainer ${ (probes.node2_portainer||{}).status||"—"} · Immich ML ${ (probes.node2_immich_ml||{}).status||"—"}`}</div></div>`;
  const anomalyHtml = anomalies.length ? anomalies.map(a=>{
    const diag = a.diagnosis ? `<div style="margin-top:6px;padding:8px;background:#0f1014;border:1px solid var(--border);border-radius:8px">${renderMarkdown(a.diagnosis)}</div>` : "";
    return `<div class="feed-item"><div><strong>${esc(a.component)}</strong> ${badge(a.severity||"warning")} <span class="muted small">tier ${esc(a.tier)} · ${esc(a.diagnosis_source||"")}</span></div><div style="margin-top:4px">${esc(a.summary||"")}</div>${diag}</div>`;
  }).join("") : `<div class="feed-item">Sin anomalías detectadas.</div>`;
  const nfsRow = nfs.name ? `<tr><td>${esc(nfs.name||"NFS /mnt/node2-storage")}</td><td>${badge(nfs.status)}</td><td class="muted small">${esc(nfs.mounted?"montado y "+(nfs.accessible?"accesible":"no accesible"):"no montado")}</td></tr>` : "";
  return `
    <div style="display:flex;flex-direction:column;gap:12px">
      <div class="feed-item"><strong>Estado general:</strong> ${badge(overallOk ? "ok":"warning")} <span class="muted small">${overallOk ? "Sin anomalías críticas detectadas" : "Se detectaron anomalías críticas o advertencias"} · ${fmtTs(audit.ts)}</span></div>
      <div><div class="card-title">Nodos</div>${node1Html}${node2Html}</div>
      <div><div class="card-title">Resumen de sondas L7</div>
        <div style="overflow:auto"><table style="width:100%;border-collapse:collapse;font-size:.9rem"><thead><tr><th style="text-align:left;padding:6px;border-bottom:1px solid var(--border)">Sonda</th><th style="text-align:left;padding:6px;border-bottom:1px solid var(--border)">Estado</th><th style="text-align:left;padding:6px;border-bottom:1px solid var(--border)">Detalle</th></tr></thead><tbody>
          ${probeKeys.map(probeRow).join("")}${nfsRow}
        </tbody></table></div>
      </div>
      <div><div class="card-title">Anomalías y diagnóstico (Gemini)</div>${anomalyHtml}</div>
      <details><summary style="cursor:pointer;color:var(--muted)">Ver JSON técnico</summary><pre class="audit-pre" style="margin-top:8px">${esc(JSON.stringify(audit, null, 2))}</pre></details>
    </div>`;
}

async function runAudit(){
  const btn = $("#auditBtn"); btn.disabled = true; btn.textContent = "Auditando...";
  auditInProgress = true;
  $("#auditModal").classList.remove("hidden");
  $("#auditResult").innerHTML = '<div class="muted">Ejecutando auditoría completa y sondas L7... Esto puede tardar; el panel sigue activo.</div>';
  try{
    const r = await fetch("/api/audit", {method:"POST"});
    const data = await r.json();
    if(data && data.audit){
      $("#auditResult").innerHTML = buildAuditReport(data.audit);
    }else{
      $("#auditResult").innerHTML = `<pre class="audit-pre">${esc(JSON.stringify(data, null, 2))}</pre>`;
    }
    await fetchState();
  }catch(e){ $("#auditResult").innerHTML = `<div class="feed-item">Error: ${esc(e.message)}</div>`; }
  finally{ auditInProgress = false; btn.disabled=false; btn.textContent="Auditar Todo con IA Ahora"; }
}

$("#auditBtn").addEventListener("click", runAudit);
$("#closeAudit").addEventListener("click", ()=>$("#auditModal").classList.add("hidden"));

$("#askForm").addEventListener("submit", async (e)=>{
  e.preventDefault();
  const input = $("#askInput"); const q = input.value.trim(); if(!q) return;
  const log = $("#chatLog");
  log.insertAdjacentHTML("beforeend", `<div class="msg user">${esc(q)}</div>`);
  input.value=""; log.scrollTop = log.scrollHeight;
  try{
    const r = await fetch("/api/ask", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({question:q})});
    const data = await r.json();
    const ans = data.answer || "(sin respuesta)";
    log.insertAdjacentHTML("beforeend", `<div class="msg bot md">${renderMarkdown(ans)}</div>`);
  }catch(err){ log.insertAdjacentHTML("beforeend", `<div class="msg bot">Error: ${esc(err.message)}</div>`); }
  log.scrollTop = log.scrollHeight;
});

fetchState();
setInterval(fetchState, 10000);
