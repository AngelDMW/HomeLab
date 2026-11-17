'use strict';

const config = require('../config');
const {
  formatBytes,
  formatDuration,
  progressBar,
  escapeHtml: esc,
  nowInTimezone,
} = require('../lib/format');

function containerByName(containers, name) {
  return (containers || []).find((container) => container.name === name);
}

function statusEmoji(container) {
  if (!container) return '⚪';
  if (container.health === 'unhealthy') return '🟠';
  if (container.state === 'running') return '🟢';
  if (container.state === 'restarting') return '🟡';
  return '🔴';
}

function containerSummary(containers) {
  const running = [];
  const down = [];
  for (const expected of config.expectedContainers) {
    const container = containerByName(containers, expected.name);
    if (container && container.state === 'running' && container.health !== 'unhealthy') {
      running.push(expected);
    } else {
      down.push({ expected, container });
    }
  }
  return { running, down };
}

function buildTelemetry(metrics, containers, options = {}) {
  const title = options.startup
    ? '🚀 <b>OPTIPLEX BRAIN ONLINE</b>'
    : '🛰️ <b>OPTIPLEX MISSION CONTROL</b>';
  const { running, down } = containerSummary(containers);
  const network = metrics.network || {};
  const internet = network.internet;
  const tailscale = network.tailscale;

  const lines = [
    title,
    `<i>${esc(metrics.hostname)} · ${esc(nowInTimezone(config.timezone))}</i>`,
    '━━━━━━━━━━━━━━━━━━━━',
    '🖥️ <b>Sistema</b>',
    `• Uptime: <b>${formatDuration(metrics.uptime)}</b> · Carga: <b>${(metrics.load?.one || 0).toFixed(2)}</b> (${metrics.cpu?.count || '?'} cores)`,
    `• CPU: <b>${(metrics.cpu?.percent || 0).toFixed(1)}%</b> ${progressBar(metrics.cpu?.percent || 0)}`,
  ];

  if (metrics.memory) {
    lines.push(
      `• RAM: <b>${formatBytes(metrics.memory.used)}</b> / ${formatBytes(metrics.memory.total)} (${metrics.memory.percent.toFixed(0)}%) ${progressBar(metrics.memory.percent)}`,
    );
  }

  lines.push(
    `• Temp: <b>${metrics.temperature ? `${metrics.temperature.celsius.toFixed(1)} °C` : '—'}</b>${metrics.temperature?.source ? ` <i>(${esc(metrics.temperature.source)})</i>` : ''}`,
    `• Disco: <b>${formatBytes(metrics.disk.free)} libres</b> de ${formatBytes(metrics.disk.total)} (${metrics.disk.percent.toFixed(0)}% usado) ${progressBar(metrics.disk.percent)}`,
    '',
    '🌐 <b>Red</b>',
    `• Internet: ${internet ? (internet.ok ? `🟢 ${internet.ms} ms` : '🔴 caído') : '⚪ sin datos'}`,
    `• Tailscale: ${tailscale ? (tailscale.ok ? `🟢 enlazado (${tailscale.ms} ms)` : '🔴 sin enlace') : '⚪ sin datos'}`,
    '',
    '🐳 <b>Flota Docker</b>',
    `• ${running.length}/${config.expectedContainers.length} servicios operativos`,
  );

  if (down.length > 0) {
    lines.push(
      `• Atención: ${down
        .map(({ expected, container }) => `${statusEmoji(container)} ${esc(expected.label)}`)
        .join(' · ')}`,
    );
  } else {
    lines.push('• Todos los servicios responden ✅');
  }

  lines.push('', `<i>Toca un botón para navegar · ${config.version}</i>`);
  return lines.join('\n');
}

function buildContainers(containers) {
  const lines = ['🐳 <b>FLOTA DE CONTENEDORES</b>', '━━━━━━━━━━━━━━━━━━━━'];
  for (const expected of config.expectedContainers) {
    const container = containerByName(containers, expected.name);
    if (!container) {
      lines.push(`⚪ <b>${esc(expected.label)}</b> · ausente`);
      continue;
    }
    const detail = container.health
      ? `${container.state}/${container.health}`
      : container.state;
    lines.push(
      `${statusEmoji(container)} <b>${esc(expected.label)}</b> · ${esc(detail)}`,
    );
  }
  lines.push('', '<i>Selecciona un servicio para reiniciarlo.</i>');
  return lines.join('\n');
}

function buildQueue(torrents, requests = [], error = null) {
  const lines = ['🎬 <b>COLA DE DESCARGAS</b>', '━━━━━━━━━━━━━━━━━━━━'];
  if (error) {
    lines.push(`🔴 qBittorrent: ${esc(error)}`);
  } else {
    const active = torrents.filter(
      (torrent) =>
        Number(torrent.progress) < 1 &&
        !String(torrent.state).startsWith('paused') &&
        !['error', 'missingFiles'].includes(torrent.state),
    );
    const paused = torrents.filter((torrent) =>
      String(torrent.state).startsWith('paused'),
    );
    const completed = torrents.filter((torrent) => Number(torrent.progress) >= 1);
    const downSpeed = active.reduce(
      (sum, torrent) => sum + (Number(torrent.dlspeed) || 0),
      0,
    );
    const upSpeed = active.reduce(
      (sum, torrent) => sum + (Number(torrent.upspeed) || 0),
      0,
    );

    lines.push(
      `⚡ ↓ ${formatBytes(downSpeed)}/s · ↑ ${formatBytes(upSpeed)}/s`,
      `Activas: <b>${active.length}</b> · Pausadas: <b>${paused.length}</b> · Completas: <b>${completed.length}</b>`,
    );

    if (active.length > 0) {
      lines.push('', '<b>Descargando</b>');
      for (const torrent of active.slice(0, 6)) {
        const percent = (Number(torrent.progress) || 0) * 100;
        const eta =
          Number(torrent.eta) > 0 && Number(torrent.eta) < 7 * 86400
            ? ` · ETA ${formatDuration(torrent.eta)}`
            : '';
        lines.push(
          `• <b>${esc(torrent.name)}</b>\n  ${progressBar(percent)} ${percent.toFixed(0)}% · ${formatBytes(torrent.size)} · ${torrent.num_seeds ?? 0} seeds${eta}`,
        );
      }
      if (active.length > 6) lines.push(`<i>… y ${active.length - 6} más</i>`);
    } else {
      lines.push('', '💤 Sin descargas activas.');
    }
  }

  lines.push('', '📥 <b>Solicitudes Seerr</b>');
  if (requests.length === 0) {
    lines.push('• Sin solicitudes recientes.');
  } else {
    for (const request of requests.slice(0, 4)) {
      lines.push(
        `• <b>${esc(request.title)}</b> · ${request.type === 'movie' ? 'Película' : 'Serie'} · ${esc(request.statusLabel)}`,
      );
    }
  }
  return lines.join('\n');
}

function buildBriefing({ metrics, containers, downloads = [], pihole = null }) {
  const { running, down } = containerSummary(containers);
  const date = new Date().toLocaleDateString('es-DO', {
    timeZone: config.timezone,
    weekday: 'long',
    day: 'numeric',
    month: 'long',
  });
  const lines = [
    '☀️ <b>MORNING BRIEFING — OPTIPLEX</b>',
    `<i>${esc(date)} · ${esc(nowInTimezone(config.timezone))}</i>`,
    '━━━━━━━━━━━━━━━━━━━━',
    `🖥️ Sistemas: <b>${running.length}/${config.expectedContainers.length}</b> operativos${down.length ? ` · ⚠️ ${down.map(({ expected }) => esc(expected.label)).join(', ')}` : ' ✅'}`,
    `💾 Disco: <b>${formatBytes(metrics.disk.free)} libres</b> de ${formatBytes(metrics.disk.total)} (${metrics.disk.percent.toFixed(0)}% usado)`,
    `🌡️ CPU: ${(metrics.cpu?.percent || 0).toFixed(0)}% · ${metrics.temperature ? `${metrics.temperature.celsius.toFixed(1)} °C` : 'temp. n/d'} · RAM ${metrics.memory ? `${metrics.memory.percent.toFixed(0)}%` : 'n/d'}`,
    `🌐 Internet: ${metrics.network?.internet?.ok ? `🟢 ${metrics.network.internet.ms} ms` : '🔴 sin conexión'} · Tailscale ${metrics.network?.tailscale?.ok ? '🟢' : '🔴'}`,
    '',
    `🎬 <b>Media importada (24 h)</b>`,
  ];

  if (downloads.length === 0) {
    lines.push('• Sin importaciones en las últimas 24 horas.');
  } else {
    for (const item of downloads.slice(0, 10)) {
      lines.push(`• ${item.type === 'Serie' ? '📺' : '🎞️'} ${esc(item.title)}`);
    }
    if (downloads.length > 10) lines.push(`<i>… y ${downloads.length - 10} más</i>`);
  }

  lines.push('', '🛡️ <b>Pi-hole</b>');
  if (pihole) {
    lines.push(
      `• ${pihole.queries.total} consultas · <b>${pihole.queries.blocked}</b> bloqueadas (${pihole.queries.percentBlocked.toFixed(1)}%)`,
    );
  } else {
    lines.push('• Sin datos de Pi-hole.');
  }

  lines.push('', `<i>Reporte generado por Optiplex Brain ${config.version}</i>`);
  return lines.join('\n');
}

function buildStorageAlert({ metrics, breakdown, level }) {
  const critical = level === 'critical';
  const lines = [
    critical
      ? '🚨 <b>ALMACENAMIENTO CRÍTICO</b>'
      : '⚠️ <b>ALERTA PREVENTIVA DE ALMACENAMIENTO</b>',
    `Libre: <b>${formatBytes(metrics.disk.free)}</b> de ${formatBytes(metrics.disk.total)} (${metrics.disk.percent.toFixed(0)}% usado)`,
    critical
      ? 'Las descargas nuevas se pausaron para proteger el sistema operativo.'
      : `Quedan menos de ${config.thresholds.diskWarnGB} GB libres.`,
    '',
    '📦 <b>Principales consumidores</b>',
  ];
  for (const entry of breakdown.slice(0, 6)) {
    lines.push(`• ${esc(entry.label)}: <b>${formatBytes(entry.bytes)}</b>`);
  }
  lines.push('', '<i>Usa los botones para liberar espacio sin tocar tus datos.</i>');
  return lines.join('\n');
}

function buildStorageDetail(metrics, breakdown) {
  const lines = [
    '💾 <b>GUARDIÁN DE ALMACENAMIENTO</b>',
    `Libre: <b>${formatBytes(metrics.disk.free)}</b> de ${formatBytes(metrics.disk.total)}`,
    `${progressBar(metrics.disk.percent)} ${metrics.disk.percent.toFixed(0)}% usado`,
    `Umbrales: ⚠️ ${config.thresholds.diskWarnGB} GB · 🚨 ${config.thresholds.diskCriticalGB} GB`,
    '',
    '📦 <b>Consumidores</b>',
  ];
  if (breakdown.length === 0) {
    lines.push('• No se pudo calcular el desglose.');
  } else {
    for (const entry of breakdown) {
      lines.push(`• ${esc(entry.label)}: <b>${formatBytes(entry.bytes)}</b>`);
    }
  }
  return lines.join('\n');
}

function buildIptv(data) {
  const lines = ['📺 <b>SALUD IPTV — JELLYFIN</b>', '━━━━━━━━━━━━━━━━━━━━'];
  if (!data || data.ok === false) {
    lines.push(`🔴 ${esc(data?.error || 'Jellyfin no responde')}`);
    return lines.join('\n');
  }
  lines.push(
    `🟢 Jellyfin <b>${esc(data.version)}</b>`,
    `• Canales M3U detectados: <b>${data.channels}</b>`,
    `• Guía EPG/XMLTV: <b>${data.epg ? '🟢 disponible' : '🟡 sin programación'}</b>`,
    '',
    `<i>${esc(data.url)}</i>`,
  );
  return lines.join('\n');
}

function dockerTotals(df) {
  const images = (df?.Images || []).reduce(
    (sum, entry) => sum + (Number(entry.Size) || 0),
    0,
  );
  const containers = (df?.Containers || []).reduce(
    (sum, entry) => sum + (Number(entry.SizeRw) || 0),
    0,
  );
  const volumes = (df?.Volumes || []).reduce(
    (sum, entry) => sum + (Number(entry.UsageData?.Size) || 0),
    0,
  );
  const buildCache = (df?.BuildCache || []).reduce(
    (sum, entry) => sum + (Number(entry.Size) || 0),
    0,
  );
  return { images, containers, volumes, buildCache };
}

function buildMaintenance(df) {
  const totals = dockerTotals(df);
  const lines = [
    '🧹 <b>MANTENIMIENTO RÁPIDO</b>',
    '━━━━━━━━━━━━━━━━━━━━',
    '🐳 <b>Consumo Docker aproximado</b>',
    `• Imágenes: <b>${formatBytes(totals.images)}</b>`,
    `• Contenedores: <b>${formatBytes(totals.containers)}</b>`,
    `• Volúmenes: <b>${formatBytes(totals.volumes)}</b>`,
    `• Build cache: <b>${formatBytes(totals.buildCache)}</b>`,
    '',
    '<i>Limpieza segura: solo capas huérfanas y caché de build.</i>',
  ];
  return lines.join('\n');
}

function buildDockerDf(df) {
  const totals = dockerTotals(df);
  return [
    '🐳 <b>DOCKER SYSTEM DF</b>',
    `Imágenes: ${formatBytes(totals.images)}`,
    `Contenedores: ${formatBytes(totals.containers)}`,
    `Volúmenes: ${formatBytes(totals.volumes)}`,
    `Build cache: ${formatBytes(totals.buildCache)}`,
    `Layers compartidas: ${formatBytes(df?.LayersSize || 0)}`,
  ].join('\n');
}

function buildHeaviestMovies(movies) {
  const lines = ['📋 <b>PELÍCULAS MÁS PESADAS</b>', '━━━━━━━━━━━━━━━━━━━━'];
  if (movies.length === 0) {
    lines.push('• Sin datos.');
    return lines.join('\n');
  }
  movies.forEach((movie, index) => {
    lines.push(
      `${index + 1}. <b>${esc(movie.title)}</b>${movie.year ? ` (${movie.year})` : ''} · ${formatBytes(movie.sizeOnDisk)}`,
    );
  });
  return lines.join('\n');
}

function buildSelfTest(checks) {
  const lines = ['🧪 <b>AUTODIAGNÓSTICO OPTIPLEX</b>', '━━━━━━━━━━━━━━━━━━━━'];
  for (const check of checks) {
    lines.push(
      `${check.ok ? '✅' : '🔴'} <b>${esc(check.name)}</b> · ${esc(check.detail)} <i>(${check.ms} ms)</i>`,
    );
  }
  const failed = checks.filter((check) => !check.ok).length;
  lines.push(
    '',
    failed === 0
      ? '🟢 Todas las integraciones responden.'
      : `⚠️ ${failed} integración(es) con problemas.`,
  );
  return lines.join('\n');
}

module.exports = {
  buildTelemetry,
  buildContainers,
  buildQueue,
  buildBriefing,
  buildStorageAlert,
  buildStorageDetail,
  buildIptv,
  buildMaintenance,
  buildDockerDf,
  buildHeaviestMovies,
  buildSelfTest,
  containerByName,
  statusEmoji,
};
