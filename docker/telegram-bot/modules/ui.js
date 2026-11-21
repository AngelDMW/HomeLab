'use strict';

const config = require('../config');
const telemetry = require('./telemetry');
const {
  formatBytes,
  formatDuration,
  progressBar,
  escapeHtml: esc,
  nowInTimezone,
} = require('../lib/format');

function mainKeyboard() {
  return [
    [
      { text: '📊 Telemetría', callback_data: 'view:telemetry' },
      { text: '🐳 Contenedores', callback_data: 'view:containers' },
    ],
    [
      { text: '🎬 Descargas', callback_data: 'view:queue' },
      { text: '📺 Salud IPTV', callback_data: 'view:iptv' },
    ],
    [
      { text: '💾 Almacenamiento', callback_data: 'view:storage' },
      { text: '🧹 Mantenimiento', callback_data: 'view:maint' },
    ],
    [
      { text: '☀️ Briefing', callback_data: 'view:briefing' },
      { text: '🖥️ Nodo 2', callback_data: 'view:node2' },
    ],
  ];
}

function backKeyboard() {
  return [[{ text: '⬅️ Menú principal', callback_data: 'view:back' }]];
}

function mainView(metrics, containers, options = {}) {
  return {
    text: telemetry.buildTelemetry(metrics, containers, options),
    keyboard: mainKeyboard(),
  };
}

function containersView(containers) {
  const rows = [];
  const entries = config.expectedContainers.filter(
    (entry) => entry.name !== config.selfName,
  );
  for (let index = 0; index < entries.length; index += 2) {
    const row = entries.slice(index, index + 2).map((entry) => ({
      text: `🔄 ${entry.label}`,
      callback_data: `restart:${entry.name}`,
    }));
    rows.push(row);
  }
  rows.push([{ text: '⬅️ Menú principal', callback_data: 'view:back' }]);
  return { text: telemetry.buildContainers(containers), keyboard: rows };
}

function queueView(torrents, requests, error = null) {
  return {
    text: telemetry.buildQueue(torrents, requests, error),
    keyboard: [
      [
        { text: '⏸️ Pausar todo', callback_data: 'queue:pause' },
        { text: '▶️ Reanudar todo', callback_data: 'queue:resume' },
      ],
      [
        { text: '🔄 Actualizar', callback_data: 'view:queue' },
        { text: '⬅️ Menú', callback_data: 'view:back' },
      ],
    ],
  };
}

function iptvView(data) {
  return {
    text: telemetry.buildIptv(data),
    keyboard: [
      [
        { text: '🔄 Verificar', callback_data: 'view:iptv' },
        { text: '⬅️ Menú', callback_data: 'view:back' },
      ],
    ],
  };
}

function maintenanceView(df) {
  return {
    text: telemetry.buildMaintenance(df),
    keyboard: [
      [
        { text: '🧹 Purgar imágenes huérfanas', callback_data: 'maint:prune' },
      ],
      [
        { text: '🗃️ Limpiar build cache', callback_data: 'maint:builder' },
        { text: '📊 Docker DF', callback_data: 'maint:df' },
      ],
      [
        { text: '📋 Películas más pesadas', callback_data: 'maint:heaviest' },
      ],
      [
        { text: '🔄 Actualizar', callback_data: 'view:maint' },
        { text: '⬅️ Menú', callback_data: 'view:back' },
      ],
    ],
  };
}

function briefingView(text) {
  return {
    text,
    keyboard: [
      [
        { text: '🔄 Regenerar', callback_data: 'view:briefing' },
        { text: '⬅️ Menú', callback_data: 'view:back' },
      ],
    ],
  };
}

function storageView(metrics, breakdown) {
  return {
    text: telemetry.buildStorageDetail(metrics, breakdown),
    keyboard: [
      [
        { text: '🧹 Limpiar caché', callback_data: 'maint:prune' },
        { text: '📋 Más pesadas', callback_data: 'maint:heaviest' },
      ],
      [
        { text: '🔄 Actualizar', callback_data: 'view:storage' },
        { text: '⬅️ Menú', callback_data: 'view:back' },
      ],
    ],
  };
}

function storageAlertKeyboard() {
  return [
    [
      { text: '🧹 Limpiar caché Docker', callback_data: 'maint:prune' },
      { text: '📋 Películas más pesadas', callback_data: 'maint:heaviest' },
    ],
    [{ text: '📊 Ver almacenamiento', callback_data: 'view:storage' }],
  ];
}

function node2View(status) {
  const name = status?.name || config.node2.name;
  const lines = [
    `🖥️ <b>${esc(String(name).toUpperCase())} — HOMELAB-NODE2</b>`,
    '━━━━━━━━━━━━━━━━━━━━',
  ];

  if (!status?.online) {
    lines.push(
      '🔴 <b>Apagado o inaccesible</b>',
      `• Host: <code>${esc(status?.host || config.node2.host)}</code>`,
    );
    if (config.node2.tailscaleHost) {
      lines.push(`• Tailscale: <code>${esc(config.node2.tailscaleHost)}</code>`);
    }
    lines.push(`• MAC: <code>${esc(config.node2.mac)}</code>`);
    if (status?.error) {
      lines.push(`• Diagnóstico: <i>${esc(status.error)}</i>`);
    }
    lines.push('', '<i>Envía un magic packet para encenderlo por Wake-on-LAN.</i>');
    return {
      text: lines.join('\n'),
      keyboard: [
        [{ text: '⚡ Encender (WoL)', callback_data: 'node2:wake' }],
        [
          { text: '🔄 Reintentar', callback_data: 'view:node2' },
          { text: '⬅️ Menú', callback_data: 'view:back' },
        ],
      ],
    };
  }

  if (status.ssh === false) {
    lines.push(
      '🟠 <b>En línea, sin acceso SSH</b>',
      `• Host: <code>${esc(status.host)}</code>`,
      `• Error: <i>${esc(status.error || 'clave no autorizada')}</i>`,
      '',
      '<i>Revisa la clave del bot en el Nodo 2.</i>',
    );
    return {
      text: lines.join('\n'),
      keyboard: [
        [
          { text: '🔄 Reintentar', callback_data: 'view:node2' },
          { text: '⬅️ Menú', callback_data: 'view:back' },
        ],
      ],
    };
  }

  lines.push(
    `🟢 <b>En línea</b> · <i>${esc(status.hostname || 'homelab-node2')}</i>`,
    `• IP: <code>${esc(status.host)}</code>${(status.ips || []).filter((ip) => ip !== status.host).length ? ` · ${esc((status.ips || []).filter((ip) => ip !== status.host).join(' · '))}` : ''}`,
    `• ${esc(status.os || 'Debian GNU/Linux')} · kernel ${esc(status.kernel || 'n/d')}`,
    `• Uptime: <b>${formatDuration(status.uptime)}</b> · Carga: <b>${(status.load?.[0] || 0).toFixed(2)}</b> (${status.cpus || '?'} cores)`,
    `• CPU: <b>${status.cpuPercent == null ? '—' : `${status.cpuPercent.toFixed(1)}%`}</b>${status.cpuPercent == null ? '' : ` ${progressBar(status.cpuPercent)}`}`,
  );

  if (status.memory) {
    lines.push(
      `• RAM: <b>${formatBytes(status.memory.used)}</b> / ${formatBytes(status.memory.total)} (${status.memory.percent.toFixed(0)}%) ${progressBar(status.memory.percent)}`,
    );
  }

  if (status.diskRoot) {
    lines.push(
      `• Disco /: <b>${formatBytes(status.diskRoot.free)} libres</b> de ${formatBytes(status.diskRoot.total)} (${status.diskRoot.percent.toFixed(0)}% usado)`,
    );
  }

  if (status.diskStorage) {
    lines.push(
      `• Disco ${esc(config.node2.storagePath)}: <b>${formatBytes(status.diskStorage.free)} libres</b> de ${formatBytes(status.diskStorage.total)} (${status.diskStorage.percent.toFixed(0)}% usado)`,
    );
  } else {
    lines.push(`• Disco ${esc(config.node2.storagePath)}: <i>no montado</i>`);
  }

  if (status.temperature != null) {
    lines.push(`• Temp: <b>${status.temperature.toFixed(1)} °C</b>`);
  }

  lines.push(
    `• Docker: <b>${status.docker?.running ?? '—'}/${status.docker?.total ?? '—'}</b> activos`,
  );
  if (status.gpu) {
    lines.push(`• GPU: ${esc(status.gpu)}`);
  }

  lines.push(
    '',
    `<i>${esc(config.node2.mac)} · ${esc(nowInTimezone(config.timezone))}</i>`,
  );

  return {
    text: lines.join('\n'),
    keyboard: [
      [
        { text: '🔄 Actualizar', callback_data: 'view:node2' },
        { text: '⏻ Apagar', callback_data: 'node2:shutdown' },
      ],
      [{ text: '⬅️ Menú', callback_data: 'view:back' }],
    ],
  };
}

function aiMenuKeyboard() {
  return [
    [
      { text: '📊 Estado Clúster', callback_data: 'menu:status' },
      { text: '🩺 Auditoría IA', callback_data: 'menu:audit' },
    ],
    [
      { text: '🛡️ Seguridad DNS/IPS', callback_data: 'menu:security' },
      { text: '🎬 Media & Descargas', callback_data: 'menu:media' },
    ],
    [
      { text: '🔄 Reiniciar Servicio', callback_data: 'menu:restart' },
      { text: '🧹 Limpiar Memoria IA', callback_data: 'menu:clear' },
    ],
    [
      { text: '🏢 HomeLab Corp', callback_data: 'corp:report' },
      { text: '🚀 Propuestas IA', callback_data: 'corp:proposals' },
    ],
  ];
}

function aiMenuView(text) {
  return { text, keyboard: aiMenuKeyboard() };
}

function restartServiceKeyboard(services) {
  const rows = [];
  const list = services.slice(0, 20);
  for (let index = 0; index < list.length; index += 2) {
    rows.push(
      list.slice(index, index + 2).map((service) => ({
        text: `🔄 ${service}`,
        callback_data: `ai:restart_menu:${service}`,
      })),
    );
  }
  rows.push([{ text: '⬅️ Menú IA', callback_data: 'menu:back' }]);
  return rows;
}

function restartConfirmKeyboard(service) {
  return [
    [
      { text: '✅ Confirmar reinicio', callback_data: `ai:restart_yes:${service}` },
      { text: '❌ Cancelar', callback_data: `ai:restart_cancel:${service}` },
    ],
  ];
}

function corpKeyboard() {
  return [
    [
      { text: '📊 Rendición de cuentas', callback_data: 'corp:report' },
      { text: '🚀 Propuestas IA', callback_data: 'corp:proposals' },
    ],
    [
      { text: '🩺 Ronda del Director', callback_data: 'corp:director' },
      { text: '⬅️ Menú IA', callback_data: 'menu:back' },
    ],
  ];
}

function corpProposalKeyboard(proposals) {
  const rows = (proposals || [])
    .filter((item) => item.status === 'pending')
    .slice(0, 6)
    .map((item) => [
      {
        text: `🚀 Aplicar mejora #${item.id}`,
        callback_data: `corp:apply:${item.id}`,
      },
    ]);
  rows.push([{ text: '🔄 Actualizar lista', callback_data: 'corp:proposals' }]);
  rows.push([{ text: '⬅️ HomeLab Corp', callback_data: 'corp:report' }]);
  return rows;
}

function helpText() {
  return [
    '🕹️ <b>CONSOLA DE MANDO — OPTIPLEX BRAIN</b>',
    '━━━━━━━━━━━━━━━━━━━━',
    '/menu — menú táctil con IA (2 columnas)',
    '/status — resumen del clúster por Gemini AI',
    '/audit — auditoría Sentinel AI con 15 sondas',
    '/corp — HomeLab Corp: 8 empleados, backups y propuestas',
    '/security — Pi-hole, DNSSEC y CrowdSec',
    '/media — descargas, colas y actividad reciente',
    '/clear — borrar memoria conversacional de la IA',
    '/mission · /telemetry — panel principal y telemetría',
    '/containers — estado y reinicio con un toque',
    '/queue — descargas qBittorrent y solicitudes Seerr',
    '/buscar &lt;título&gt; — buscar y solicitar una película en Seerr',
    '/disk — desglose de almacenamiento',
    '/briefing — reporte ejecutivo inmediato',
    '/iptv — salud de canales en Jellyfin',
    '/maint — mantenimiento seguro de Docker',
    '/node2 — estado, encendido (WoL) y apagado del Nodo 2',
    '/selftest — diagnóstico de todas las integraciones',
    '',
    '<b>Lenguaje natural:</b>',
    '· “estado del servidor”',
    '· “reinicia sonarr”',
    '· “pausa las descargas”',
    '· “¿cuánto espacio libre queda?”',
    '',
    '<i>Solo el CHAT_ID autorizado puede usar el bot.</i>',
  ].join('\n');
}

module.exports = {
  mainView,
  containersView,
  queueView,
  iptvView,
  maintenanceView,
  briefingView,
  storageView,
  storageAlertKeyboard,
  aiMenuKeyboard,
  aiMenuView,
  restartServiceKeyboard,
  restartConfirmKeyboard,
  corpKeyboard,
  corpProposalKeyboard,
  node2View,
  backKeyboard,
  helpText,
};
