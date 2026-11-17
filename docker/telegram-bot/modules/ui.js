'use strict';

const config = require('../config');
const telemetry = require('./telemetry');

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
      { text: '🔄 Actualizar', callback_data: 'view:refresh' },
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

function helpText() {
  return [
    '🕹️ <b>CONSOLA DE MANDO — OPTIPLEX BRAIN</b>',
    '━━━━━━━━━━━━━━━━━━━━',
    '/mission · /status — panel principal',
    '/telemetry — CPU, RAM, disco, red y sensores',
    '/containers — estado y reinicio con un toque',
    '/queue — descargas qBittorrent y solicitudes Seerr',
    '/buscar &lt;título&gt; — buscar y solicitar una película en Seerr',
    '/disk — desglose de almacenamiento',
    '/briefing — reporte ejecutivo inmediato',
    '/iptv — salud de canales en Jellyfin',
    '/maint — mantenimiento seguro de Docker',
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
  backKeyboard,
  helpText,
};
