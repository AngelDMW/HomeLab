'use strict';

const TelegramBot = require('node-telegram-bot-api');
const config = require('./config');
const logger = require('./lib/logger');
const store = require('./lib/store');
const {
  sleep,
  escapeHtml: esc,
  htmlToPlain,
  formatBytes,
} = require('./lib/format');
const { rawRequest, getJson } = require('./lib/http');
const docker = require('./modules/dockerClient');
const host = require('./modules/hostMetrics');
const telemetry = require('./modules/telemetry');
const ui = require('./modules/ui');
const { radarr, sonarr } = require('./modules/arr');
const { seerr } = require('./modules/seerr');
const { jellyfin } = require('./modules/jellyfin');
const { qbittorrent } = require('./modules/qbittorrent');
const { pihole } = require('./modules/pihole');
const SelfHeal = require('./modules/selfHeal');
const StorageGuardian = require('./modules/storageGuardian');
const { EventBus } = require('./modules/eventBus');
const { SecurityWatcher } = require('./modules/securityWatcher');
const Scheduler = require('./modules/scheduler');
const ai = require('./modules/ai');
const node2 = require('./modules/node2');
const SentinelBridge = require('./modules/sentinelBridge');

const SELFTEST = process.argv.includes('--selftest');

if (!config.telegram.token || !config.telegram.chatId) {
  logger.error('Faltan TELEGRAM_TOKEN o CHAT_ID en el archivo .env');
  process.exit(1);
}

const bot = new TelegramBot(
  config.telegram.token,
  SELFTEST
    ? { polling: false }
    : {
        polling: { interval: 1200, autoStart: true, params: { timeout: 15 } },
        request: { family: 4 },
      },
);

async function notify(text, extra = {}) {
  const body = String(text || '').slice(0, 4000);
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      return await bot.sendMessage(config.telegram.chatId, body, {
        parse_mode: 'HTML',
        disable_web_page_preview: true,
        ...extra,
      });
    } catch (error) {
      // La IA genera HTML; si Telegram rechaza entidades, reenviamos texto plano.
      if (
        /can't parse entities|parse entities/i.test(error.message || '') &&
        extra.parse_mode === 'HTML'
      ) {
        try {
          const plainOptions = { ...extra };
          delete plainOptions.parse_mode;
          return await bot.sendMessage(config.telegram.chatId, htmlToPlain(body), {
            disable_web_page_preview: true,
            ...plainOptions,
          });
        } catch (plainError) {
          logger.warn(`[telegram] fallback texto plano: ${plainError.message}`);
        }
      }
      logger.warn(`[telegram] envío falló (${attempt}/3): ${error.message}`);
      if (attempt < 3) await sleep(1500 * attempt);
    }
  }
  return null;
}

async function sendPlain(text) {
  try {
    return await bot.sendMessage(
      config.telegram.chatId,
      String(text || '').slice(0, 4000),
    );
  } catch (error) {
    logger.warn(`[telegram] envío plano falló: ${error.message}`);
    return null;
  }
}

async function brainRequest(endpoint, payload) {
  const response = await rawRequest(`${config.ai.brainUrl}${endpoint}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: payload,
    timeout: 180000,
    maxBytes: 4 * 1024 * 1024,
  });
  const data = response.data && typeof response.data === 'object'
    ? response.data
    : {};
  if (response.status !== 200 || !data.ok) {
    throw new Error(data.error || `AI brain HTTP ${response.status}`);
  }
  return data.text || '';
}

async function askAi(prompt, options = {}) {
  if (options.audioBuffer) {
    return brainRequest('/voice', {
      chat_id: config.telegram.chatId,
      text: prompt,
      audio_b64: options.audioBuffer.toString('base64'),
      mime_type: options.mimeType || 'audio/ogg',
    });
  }
  return brainRequest('/ask', {
    chat_id: config.telegram.chatId,
    text: prompt,
  });
}

function aiBackKeyboard() {
  return [[{ text: '⬅️ Menú IA', callback_data: 'menu:back' }]];
}

async function sendAi(prompt, keyboard = []) {
  await bot.sendChatAction(config.telegram.chatId, 'typing').catch(() => {});
  const answer = await askAi(prompt);
  return notify(answer, {
    reply_markup: { inline_keyboard: [...keyboard, ...aiBackKeyboard()] },
  });
}

async function showAiMenu(messageId = null) {
  await renderPanel(
    messageId,
    ui.aiMenuView(
      [
        '🤖 <b>HOMEFLIX AI — MENÚ PRINCIPAL</b>',
        '━━━━━━━━━━━━━━━━━━━━',
        'Puedo consultar el clúster, auditar los 15 sensores de Sentinel, coordinar HomeLab Corp (8 empleados), revisar seguridad y medios, o reiniciar servicios previa confirmación.',
        '',
        '<i>Memoria conversacional: últimos 12 turnos. Usa /clear para borrarla.</i>',
      ].join('\n'),
    ),
  );
}

async function showRestartMenu(messageId = null) {
  await renderPanel(messageId, {
    text: [
      '🔄 <b>REINICIAR SERVICIO</b>',
      '━━━━━━━━━━━━━━━━━━━━',
      'Selecciona un servicio permitido. La orden se ejecuta solo tras tu confirmación explícita.',
    ].join('\n'),
    keyboard: ui.restartServiceKeyboard(config.restartWhitelist),
  });
}

async function clearAiMemory() {
  try {
    await brainRequest('/clear', { chat_id: config.telegram.chatId });
    return notify('🧹 Memoria conversacional de la IA eliminada para este chat.');
  } catch (error) {
    logger.warn(`[ai] clear: ${error.message}`);
    return notify(`⚠️ No pude limpiar la memoria IA: ${esc(error.message)}`);
  }
}

function isAllowedId(id) {
  return config.telegram.allowedChatIds.map(Number).includes(Number(id));
}

function isOwnerChat(chatId) {
  return isAllowedId(chatId);
}

function guard(message) {
  const chatOk = message?.chat && isOwnerChat(message.chat.id);
  const fromId = message?.from?.id;
  const fromOk = fromId == null || isOwnerChat(fromId);
  if (chatOk && fromOk) return true;
  logger.warn(
    `[security] mensaje no autorizado chat=${message?.chat?.id} user=${message?.from?.username || fromId}`,
  );
  return false;
}

async function ack(query, text) {
  if (query.__acked) return;
  query.__acked = true;
  try {
    await bot.answerCallbackQuery(query.id, text ? { text } : {});
  } catch (error) {
    logger.debug(`[telegram] answerCallbackQuery: ${error.message}`);
  }
}

async function renderPanel(messageId, view) {
  const options = {
    parse_mode: 'HTML',
    disable_web_page_preview: true,
    reply_markup: { inline_keyboard: view.keyboard },
  };
  if (messageId) {
    try {
      await bot.editMessageText(view.text, {
        chat_id: config.telegram.chatId,
        message_id: messageId,
        ...options,
      });
      return;
    } catch (error) {
      if (/message is not modified/i.test(error.message || '')) return;
      logger.debug(`[telegram] edit fallback: ${error.message}`);
    }
  }
  await notify(view.text, { reply_markup: options.reply_markup });
}

async function clearButtons(messageId) {
  if (!messageId) return;
  try {
    await bot.editMessageReplyMarkup(
      { inline_keyboard: [] },
      { chat_id: config.telegram.chatId, message_id: messageId },
    );
  } catch (error) {
    logger.debug(`[telegram] clear buttons: ${error.message}`);
  }
}

const pendingMovies = new Map();

async function searchAndRequestMovies(term) {
  await bot.sendChatAction(config.telegram.chatId, 'typing').catch(() => {});
  const results = await seerr.searchMovies(term);
  if (results.length === 0) {
    return notify(`🔎 Sin resultados de películas para <b>${esc(term)}</b>.`);
  }

  const lines = [
    '🔎 <b>BUSCAR PELÍCULA</b>',
    `<i>Consulta: ${esc(term)}</i>`,
    '━━━━━━━━━━━━━━━━━━━━',
  ];
  const rows = [];
  for (const movie of results) {
    pendingMovies.set(String(movie.id), movie);
    lines.push(
      `• <b>${esc(movie.title)}</b>${movie.year ? ` (${movie.year})` : ''} · ${esc(movie.statusLabel)}`,
    );
    if (movie.status === 0 || movie.status === 1) {
      const label = `📥 ${movie.title.slice(0, 30)}${movie.year ? ` (${movie.year})` : ''}`;
      rows.push([
        { text: label, callback_data: `request:movie:${movie.id}` },
      ]);
    }
  }
  rows.push([{ text: '⬅️ Menú principal', callback_data: 'view:back' }]);
  lines.push('', '<i>Toca una película para enviarla a Seerr.</i>');
  await notify(lines.join('\n'), {
    reply_markup: { inline_keyboard: rows },
  });
}

async function requestMovie(id, cached = null) {
  const details = await seerr.mediaDetails('movie', id).catch(() => null);
  const status = Number(details?.mediaInfo?.status) || 0;
  const title = cached?.title || details?.title || `TMDB ${id}`;
  if (status >= 2) {
    return notify(
      `ℹ️ <b>${esc(title)}</b> ya está en Seerr: ${esc(seerr.mediaStatusLabel(status))}.`,
    );
  }
  await seerr.createRequest('movie', id);
  return notify(
    `✅ Solicitud enviada a Seerr para <b>${esc(title)}</b>.\nRadarr la procesará según tus perfiles.`,
    {
      reply_markup: {
        inline_keyboard: [
          [{ text: '🔗 Ver en Seerr', url: config.urls.seerr }],
        ],
      },
    },
  );
}

async function getMainView(options = {}) {
  const [metrics, containers] = await Promise.all([
    host.collect(),
    docker.listContainers(),
  ]);
  return ui.mainView(metrics, containers, options);
}

async function showMain(messageId = null) {
  await renderPanel(messageId, await getMainView());
}

async function showContainers(messageId = null) {
  const containers = await docker.listContainers();
  await renderPanel(messageId, ui.containersView(containers));
}

async function showQueue(messageId = null) {
  let torrents = [];
  let error = null;
  try {
    torrents = await qbittorrent.torrents();
  } catch (caught) {
    error = caught.message;
  }
  const requests = await eventBus.getRequests().catch(() => []);
  await renderPanel(messageId, ui.queueView(torrents, requests, error));
}

async function showIptv(messageId = null) {
  const data = await jellyfin.iptv().catch((error) => ({
    ok: false,
    error: error.message,
  }));
  await renderPanel(messageId, ui.iptvView(data));
}

async function showMaintenance(messageId = null) {
  const df = await docker.systemDf().catch(() => null);
  await renderPanel(messageId, ui.maintenanceView(df));
}

async function showStorage(messageId = null) {
  const [metrics, breakdown] = await Promise.all([
    host.collect(),
    storage.breakdown(),
  ]);
  await renderPanel(messageId, ui.storageView(metrics, breakdown));
}

async function showBriefing(messageId = null) {
  const text = await scheduler.buildBriefingText();
  await renderPanel(messageId, ui.briefingView(text));
}

async function sendManualBriefing() {
  const text = await scheduler.buildBriefingText();
  await notify(text, { reply_markup: { inline_keyboard: ui.backKeyboard() } });
}

async function showNode2(messageId = null) {
  const status = await node2.status();
  await renderPanel(messageId, ui.node2View(status));
}

async function wakeNode2() {
  const targets = await node2.wake();
  logger.info(`[node2] WoL solicitado (${targets.length} paquetes)`);
  return notify(
    `⚡ <b>Magic packet enviado</b> a ${esc(config.node2.name)} (<code>${esc(config.node2.mac)}</code>).\nEl equipo suele tardar 30-60 s en arrancar.`,
    {
      reply_markup: {
        inline_keyboard: [[{ text: '🔄 Ver estado', callback_data: 'view:node2' }]],
      },
    },
  );
}

async function confirmNode2Shutdown() {
  return notify(
    `⚠️ ¿Apagar <b>${esc(config.node2.name)}</b> (<code>${esc(config.node2.host)}</code>)?\nSe cerrarán Docker y todos sus servicios.`,
    {
      reply_markup: {
        inline_keyboard: [
          [
            { text: '✅ Sí, apagar', callback_data: 'node2:shutdown_yes' },
            { text: '❌ Cancelar', callback_data: 'node2:shutdown_cancel' },
          ],
        ],
      },
    },
  );
}

async function performNode2Shutdown(messageId = null) {
  await notify(`⏻ Apagando <b>${esc(config.node2.name)}</b>…`);
  const result = await node2.shutdown();
  if (!result.ok) {
    return notify(
      `❌ No pude apagar el Nodo 2: ${esc(result.error || 'sin acceso SSH')}`,
    );
  }
  await sleep(9000);
  await notify(`✅ Orden de apagado enviada a <b>${esc(config.node2.name)}</b>.`);
  if (messageId) await showNode2(messageId);
}

function resolveService(input) {
  const raw = String(input || '')
    .toLowerCase()
    .replace(/\s+/g, '');
  const aliases = {
    seerr: 'jellyseerr',
    jellyseerr: 'jellyseerr',
    qbit: 'qbittorrent',
    torrent: 'qbittorrent',
    bot: 'optiplex-chatops',
    telegram: 'optiplex-chatops',
    chatops: 'optiplex-chatops',
    pi: 'pihole',
    pihole: 'pihole',
  };
  const name = aliases[raw] || raw;
  return config.expectedContainers.find((entry) => entry.name === name);
}

async function restartByName(input) {
  const service = resolveService(input);
  if (!service || !config.restartWhitelist.includes(service.name)) {
    return notify(
      'Servicio no permitido. Usa /menu → 🔄 Reiniciar Servicio para ver la lista blanca.',
    );
  }
  if (service.name === config.selfName) {
    return notify('No puedo reiniciarme a mí mismo desde mi propio proceso.');
  }

  await notify(`🔄 Reiniciando <b>${esc(service.label)}</b>…`);
  const ok = await docker.restartContainer(service.name);
  await sleep(4000);
  if (!ok) {
    return notify(`❌ No pude reiniciar <b>${esc(service.label)}</b>. Revisa los logs.`);
  }
  const container = (await docker.listContainers()).find(
    (entry) => entry.name === service.name,
  );
  return notify(
    `✅ <b>${esc(service.label)}</b> reiniciado.\nEstado: ${esc(container?.status || 'desconocido')}`,
  );
}

async function searchAlternativeFor(torrent) {
  const isSeries =
    /sonarr|tv/i.test(torrent.category || '') ||
    /\bS\d{1,2}E\d{1,2}\b/i.test(torrent.name || '');
  const client = isSeries ? sonarr : radarr;
  const result = await client.searchAlternative(torrent.name);
  if (result.ok) {
    return `🔄 Búsqueda iniciada en <b>${esc(result.service)}</b> para <b>${esc(result.title)}</b>.`;
  }
  return `⚠️ ${esc(result.message)}`;
}

async function corpRequest(path, options = {}) {
  const response = await rawRequest(`${config.corp.apiUrl}${path}`, {
    timeout: options.timeout || 30,
    method: options.method || 'GET',
    body: options.body || null,
    headers: config.corp.token
      ? { 'X-Corp-Token': config.corp.token }
      : {},
  });
  if (response.status < 200 || response.status >= 300) {
    throw new Error(`Corp API HTTP ${response.status}`);
  }
  return response.data && typeof response.data === 'object' ? response.data : {};
}

async function showCorpReport(messageId = null, directive = '') {
  await renderPanel(messageId, {
    text: '🏢 <b>HOMELAB CORP</b>\n━━━━━━━━━━━━━━━━━━━━\nConsultando a los 8 empleados y al Director…',
    keyboard: ui.corpKeyboard(),
  });
  try {
    const summary = await corpRequest('/api/ledger/summary');
    const extras = {};
    for (const [key, path] of [
      ['backups', '/api/tools/backup/history'],
      ['updates', '/api/tools/updater/history'],
    ]) {
      try {
        const history = await corpRequest(path);
        extras[key] = history[key] || [];
      } catch (error) {
        extras[key] = [];
      }
    }
    const employees = summary.employees || [];
    const lines = [
      '🏢 <b>HOMELAB CORP — 8 EMPLEADOS</b>',
      '━━━━━━━━━━━━━━━━━━━━',
    ];
    if (directive) {
      try {
        const round = await rawRequest(config.corp.directorUrl, {
          method: 'POST',
          timeout: 240,
          body: { directive, requested_by: 'telegram' },
        });
        if (round.status === 200 && round.data) {
          const report = String(round.data.report || '').trim();
          lines.push(`<b>Ronda ${esc(round.data.round_id || '')}:</b>`);
          lines.push(esc(report.slice(0, 1800)));
          lines.push('');
        }
      } catch (error) {
        lines.push(`⚠️ Director no disponible: ${esc(error.message)}`);
        lines.push('');
      }
    }
    for (const item of employees) {
      lines.push(
        `• <b>${esc(item.employee)}</b>: ${item.reports || 0} informes · ` +
          `calidad ${esc(item.avg_quality || 'n/d')} · ${esc(item.last_severity || 'sin datos')}`,
      );
    }
    if (extras.backups.length) {
      const last = extras.backups[0];
      lines.push('');
      lines.push(
        `💾 Último respaldo: <b>${esc(last.name || last.id || '?')}</b> · ` +
          `${esc(last.integrity || 'n/d')} · ${esc(last.bytes_total || '?')} bytes`,
      );
    }
    if (extras.updates.length) {
      const last = extras.updates[0];
      lines.push(
        `🔄 Última actualización: <b>${esc(last.service || '?')}</b> · ` +
          `rollback ${last.rolled_back ? 'sí' : 'no'} · ok ${last.ok}`,
      );
    }
    const tasks = summary.recent_tasks || [];
    if (tasks.length) {
      lines.push(`📋 Órdenes recientes: ${tasks.length} · última #${tasks[0].id}.`);
    }
    await renderPanel(messageId, {
      text: lines.join('\n'),
      keyboard: ui.corpKeyboard(),
    });
  } catch (error) {
    await renderPanel(messageId, {
      text: `⚠️ No pude consultar HomeLab Corp: ${esc(error.message)}`,
      keyboard: ui.corpKeyboard(),
    });
  }
}

async function showCorpProposals(messageId = null) {
  try {
    const body = await corpRequest('/api/ledger/proposals');
    const proposals = (body.proposals || []).filter((item) => item.status === 'pending');
    const lines = [
      '🚀 <b>PROPUESTAS DE AGENT-ARCHITECT</b>',
      '━━━━━━━━━━━━━━━━━━━━',
      proposals.length
        ? 'Selecciona una mejora para aplicarla tras tu aprobación explícita.'
        : 'No hay propuestas pendientes en este momento.',
    ];
    for (const item of proposals.slice(0, 6)) {
      lines.push(`• #${item.id} · ${esc(item.title || 'Mejora')} · ${esc(item.risk || 'riesgo n/d')}`);
    }
    await renderPanel(messageId, {
      text: lines.join('\n'),
      keyboard: ui.corpProposalKeyboard(proposals),
    });
  } catch (error) {
    await renderPanel(messageId, {
      text: `⚠️ No pude listar propuestas: ${esc(error.message)}`,
      keyboard: ui.corpKeyboard(),
    });
  }
}

async function applyCorpProposal(id, messageId = null) {
  try {
    const body = await corpRequest(`/api/ledger/proposal/${id}/apply`, { method: 'POST', timeout: 90 });
    await renderPanel(messageId, {
      text: `✅ Propuesta <b>#${esc(id)}</b> aplicada: ${esc(body.status || body.result || 'ok')}`,
      keyboard: ui.corpKeyboard(),
    });
  } catch (error) {
    await renderPanel(messageId, {
      text: `⚠️ No pude aplicar la propuesta #${esc(id)}: ${esc(error.message)}`,
      keyboard: ui.corpProposalKeyboard([]),
    });
  }
}

async function openView(action, messageId) {
  switch (action) {
    case 'telemetry':
    case 'refresh':
    case 'back':
      return showMain(messageId);
    case 'containers':
      return showContainers(messageId);
    case 'queue':
      return showQueue(messageId);
    case 'iptv':
      return showIptv(messageId);
    case 'maint':
      return showMaintenance(messageId);
    case 'storage':
      return showStorage(messageId);
    case 'briefing':
      return showBriefing(messageId);
    case 'node2':
      return showNode2(messageId);
    case 'corp':
      return showCorpReport(messageId);
    case 'corp_proposals':
      return showCorpProposals(messageId);
    default:
      return showMain(messageId);
  }
}

const COMMANDS = [
  { command: 'menu', description: 'Menú táctil con IA (2 columnas)' },
  { command: 'status', description: 'Resumen del clúster por Gemini AI' },
  { command: 'audit', description: 'Auditoría Sentinel AI (15 sondas)' },
  { command: 'security', description: 'Pi-hole, DNSSEC y CrowdSec' },
  { command: 'media', description: 'Descargas, colas y actividad' },
  { command: 'clear', description: 'Borrar memoria conversacional IA' },
  { command: 'mission', description: 'Panel de control principal' },
  { command: 'telemetry', description: 'Telemetría en vivo' },
  { command: 'containers', description: 'Estado y reinicio de contenedores' },
  { command: 'queue', description: 'Cola de descargas y solicitudes' },
  { command: 'buscar', description: 'Buscar y solicitar una película' },
  { command: 'briefing', description: 'Reporte ejecutivo ahora' },
  { command: 'iptv', description: 'Salud de IPTV en Jellyfin' },
  { command: 'disk', description: 'Detalle de almacenamiento' },
  { command: 'maint', description: 'Mantenimiento y limpieza' },
  { command: 'node2', description: 'Estado y control del Nodo 2' },
  { command: 'corp', description: 'HomeLab Corp: directivas al Director y rendición de cuentas' },
  { command: 'selftest', description: 'Diagnóstico de integraciones' },
  { command: 'help', description: 'Ayuda y comandos' },
];

async function handleCommand(text) {
  const command = text
    .slice(1)
    .trim()
    .split(/\s+/)[0]
    .split('@')[0]
    .toLowerCase();
  const argument = text.trim().split(/\s+/).slice(1).join(' ');
  const firstArgument = argument.split(/\s+/)[0];

  switch (command) {
    case 'start':
    case 'mission':
    case 'telemetry':
      return showMain();
    case 'menu':
      return showAiMenu();
    case 'status':
      return sendAi(
        'Resume el estado actual del clúster: CPU/RAM/disco del Nodo 1 y Nodo 2, contenedores activos y sondas de Sentinel AI.',
      );
    case 'audit':
      return sendAi(
        'Ejecuta la auditoría completa de Sentinel AI y resume sondas, anomalías y acciones pendientes.',
      );
    case 'security':
      return sendAi(
        'Genera el reporte de seguridad: bloqueo de Pi-hole, validación DNSSEC de Unbound y decisiones/alertas de CrowdSec IPS.',
      );
    case 'media':
      return sendAi(
        'Consulta actividad multimedia: descargas activas en qBittorrent, colas de Radarr/Sonarr y lo último en Jellyfin.',
      );
    case 'clear':
      return clearAiMemory();
    case 'containers':
      return showContainers();
    case 'queue':
      return showQueue();
    case 'buscar':
    case 'search':
      if (!argument) return notify('Uso: /buscar <título de película>');
      return searchAndRequestMovies(argument);
    case 'briefing':
      return sendManualBriefing();
    case 'iptv':
      return showIptv();
    case 'disk':
      return showStorage();
    case 'maint':
      return showMaintenance();
    case 'node2': {
      const action = String(firstArgument || '').toLowerCase();
      if (['on', 'encender', 'wake', 'wol'].includes(action)) return wakeNode2();
      if (['off', 'apagar', 'shutdown', 'poweroff'].includes(action)) {
        return confirmNode2Shutdown();
      }
      return showNode2();
    }
    case 'corp':
      if (argument === 'propuestas' || argument === 'proposals') {
        return showCorpProposals();
      }
      return showCorpReport(null, argument);
    case 'help':
      return notify(ui.helpText());
    case 'selftest':
      return runSelfTest(false);
    case 'restart':
      if (!firstArgument) return notify('Uso: /restart <servicio>');
      return restartByName(firstArgument);
    default:
      return notify('Comando no reconocido. Usa /help para ver la consola de mando.');
  }
}

async function handleNatural(text) {
  const lower = text.toLowerCase();

  if (/\b(nodo\s*2|node2|homelab-node2)\b/i.test(text)) {
    if (/\b(enciende|encender|prende|prender|wake|wol)\b/i.test(lower)) {
      return wakeNode2();
    }
    if (/\b(apaga|apagar|shutdown|poweroff)\b/i.test(lower)) {
      return confirmNode2Shutdown();
    }
    return showNode2();
  }

  if (/\b(home\s*lab\s*corp|corporaci[oó]n|director|empleados|rendici[oó]n de cuentas)\b/i.test(text)) {
    return sendAi(`Gestiona HomeLab Corp con esta instrucción: ${text}`);
  }

  const restartMatch = text.match(/^(?:reinicia|reiniciar|restart)\s+(.+)$/i);
  if (restartMatch) return restartByName(restartMatch[1]);

  const searchMatch = text.match(
    /^(?:busca|buscar|solicita|solicitar)\s+(?:una\s+)?(?:pel[ií]cula\s+)?(.+)$/i,
  );
  if (searchMatch) return searchAndRequestMovies(searchMatch[1]);

  if (/\b(estado|status|resumen|panorama)\b/.test(lower) || /c[oó]mo est[aá]/.test(lower)) {
    return showMain();
  }
  if (/\b(contenedor(es)?|docker|servicios)\b/.test(lower)) {
    return showContainers();
  }
  if (/\b(disco|almacenamiento|espacio)\b/.test(lower)) {
    return showStorage();
  }
  if (/\b(cola|descargas?|torrents?)\b/.test(lower)) {
    if (/\b(pausa|det[eé]n|para)\b/.test(lower)) {
      await qbittorrent.pauseAll();
      return notify('⏸️ Descargas pausadas desde Telegram.');
    }
    if (/\b(reanuda|reanudar|contin[uú]a|resume)\b/.test(lower)) {
      await qbittorrent.resumeAll();
      return notify('▶️ Descargas reanudadas desde Telegram.');
    }
    return showQueue();
  }
  if (/\b(iptv|canales|televisi[oó]n)\b/.test(lower)) return showIptv();
  if (/\b(briefing|reporte|resumen diario)\b/.test(lower)) return sendManualBriefing();
  if (/\b(ayuda|help|comandos)\b/.test(lower)) return notify(ui.helpText());

  try {
    await bot.sendChatAction(config.telegram.chatId, 'typing');
  } catch (error) {
    logger.debug(`[telegram] sendChatAction: ${error.message}`);
  }

  let context = '';
  try {
    const [metrics, containers] = await Promise.all([
      host.collect(),
      docker.listContainers(),
    ]);
    const running = containers.filter((entry) => entry.state === 'running').length;
    context = `Disco libre ${formatBytes(metrics.disk.free)}; CPU ${metrics.cpu.percent.toFixed(0)}%; temp ${metrics.temperature ? `${metrics.temperature.celsius.toFixed(0)} C` : 'n/d'}; contenedores ${running}/${config.expectedContainers.length}; host ${metrics.hostname}.`;
  } catch (error) {
    context = '';
  }

  try {
    const answer = await askAi(text);
    if (answer) return notify(answer, { reply_markup: { inline_keyboard: aiBackKeyboard() } });
  } catch (error) {
    logger.warn(`[ai] brain no disponible, fallback local: ${error.message}`);
    const answer = await ai.chat(text, context);
    if (answer) return sendPlain(answer);
  }
  return notify('🤖 No entendí la orden. Usa /menu o /help.');
}

async function onMessage(message) {
  if (!guard(message)) return;
  // Un update válido indica que el polling volvió a funcionar.
  onPollingError.count = 0;

  const voice = message.voice || message.audio;
  if (voice) {
    try {
      await bot.sendChatAction(config.telegram.chatId, 'typing').catch(() => {});
      const stream = await bot.getFileStream(voice.file_id);
      const chunks = [];
      for await (const chunk of stream) chunks.push(Buffer.from(chunk));
      const audioBuffer = Buffer.concat(chunks);
      const caption = String(message.caption || '').trim();
      const answer = await askAi(
        caption || 'Transcribe esta nota de voz y ejecuta la consulta o herramienta que solicite.',
        { audioBuffer, mimeType: voice.mime_type || 'audio/ogg' },
      );
      await notify(answer, {
        reply_markup: { inline_keyboard: aiBackKeyboard() },
      });
    } catch (error) {
      logger.error(`[voice] ${error.stack || error.message}`);
      await notify(`⚠️ No pude procesar la nota de voz: ${esc(error.message)}`);
    }
    return;
  }

  const text = String(message.text || '').trim();
  if (!text) return;
  try {
    if (text.startsWith('/')) {
      await handleCommand(text);
    } else {
      await handleNatural(text);
    }
  } catch (error) {
    logger.error(`[handler] ${error.stack || error.message}`);
    await notify(`⚠️ Error procesando la orden: ${esc(error.message)}`);
  }
}

async function onCallback(query) {
  const messageId = query.message?.message_id;
  const chatOk = query.message?.chat && isOwnerChat(query.message.chat.id);
  const fromOk = query.from && isAllowedId(query.from.id);
  if (!chatOk || !fromOk) {
    await ack(query, 'No autorizado');
    logger.warn(
      `[security] callback no autorizado chat=${query.message?.chat?.id} user=${query.from?.username || query.from?.id}`,
    );
    return;
  }

  const [scope, action, argument] = String(query.data || '').split(':');
  try {
    switch (scope) {
      case 'view':
        await ack(query);
        await openView(action, messageId);
        break;

      case 'node2': {
        if (action === 'wake') {
          await ack(query, 'Enviando WoL…');
          await wakeNode2();
          break;
        }
        if (action === 'shutdown') {
          await ack(query);
          await confirmNode2Shutdown();
          break;
        }
        if (action === 'shutdown_yes') {
          await ack(query, 'Apagando…');
          await performNode2Shutdown(messageId);
          break;
        }
        if (action === 'shutdown_cancel') {
          await ack(query, 'Cancelado');
          await clearButtons(messageId);
          break;
        }
        await ack(query, 'Acción desconocida');
        break;
      }

      case 'restart': {
        const service = resolveService(action);
        if (!service || service.name === config.selfName) {
          await ack(query, 'Servicio no permitido');
          break;
        }
        await ack(query, `Reiniciando ${service.label}…`);
        await notify(`🔄 Reiniciando <b>${esc(service.label)}</b>…`);
        const ok = await docker.restartContainer(service.name);
        await sleep(4000);
        await notify(
          ok
            ? `✅ <b>${esc(service.label)}</b> reiniciado.`
            : `❌ No pude reiniciar <b>${esc(service.label)}</b>.`,
        );
        await showContainers(messageId);
        break;
      }

      case 'queue': {
        if (action === 'pause') {
          await ack(query, 'Pausando descargas…');
          await qbittorrent.pauseAll();
          await notify('⏸️ Todas las descargas fueron pausadas.');
        } else if (action === 'resume') {
          await ack(query, 'Reanudando descargas…');
          await qbittorrent.resumeAll();
          await notify('▶️ Descargas reanudadas.');
        } else {
          await ack(query, 'Acción desconocida');
        }
        await showQueue(messageId);
        break;
      }

      case 'maint': {
        if (action === 'prune' || action === 'builder') {
          await ack(query, 'Limpiando…');
          const result =
            action === 'prune'
              ? await docker.pruneImages()
              : await docker.pruneBuilder();
          const freed = Number(result?.SpaceReclaimed) || 0;
          await notify(
            `🧹 Limpieza completada.\nEspacio recuperado: <b>${formatBytes(freed)}</b>`,
          );
          await showMaintenance(messageId);
        } else if (action === 'df') {
          await ack(query);
          const df = await docker.systemDf();
          await notify(telemetry.buildDockerDf(df));
        } else if (action === 'heaviest') {
          await ack(query, 'Calculando…');
          const movies = await radarr.library();
          const top = movies
            .filter((movie) => Number(movie.sizeOnDisk) > 0)
            .sort((left, right) => right.sizeOnDisk - left.sizeOnDisk)
            .slice(0, 8);
          await notify(telemetry.buildHeaviestMovies(top));
        } else {
          await ack(query, 'Acción desconocida');
        }
        break;
      }

      case 'request': {
        if (action !== 'movie') {
          await ack(query, 'Solicitud no válida');
          break;
        }
        await ack(query, 'Enviando solicitud…');
        await requestMovie(Number(argument), pendingMovies.get(String(argument)));
        break;
      }

      case 'stall': {
        if (action === 'ignore') {
          await ack(query, 'Ignorado');
          await clearButtons(messageId);
          break;
        }
        if (action === 'delete') {
          await ack(query, 'Eliminando torrent…');
          await qbittorrent.deleteTorrent(argument, false);
          await notify(
            '🗑️ Torrent eliminado de qBittorrent.\n<i>Los archivos descargados se conservaron.</i>',
          );
          await clearButtons(messageId);
          break;
        }
        if (action === 'search') {
          await ack(query, 'Buscando release alternativo…');
          const torrents = await qbittorrent.torrents();
          const torrent = torrents.find((entry) => entry.hash === argument);
          if (!torrent) {
            await notify('⚠️ El torrent ya no existe en qBittorrent.');
          } else {
            await notify(await searchAlternativeFor(torrent));
          }
          await clearButtons(messageId);
          break;
        }
        await ack(query, 'Acción desconocida');
        break;
      }

      case 'menu': {
        await ack(query);
        if (action === 'status') {
          await sendAi(
            'Resume el estado actual del clúster: CPU/RAM/disco, contenedores y sondas de Sentinel AI.',
          );
        } else if (action === 'audit') {
          await sendAi(
            'Ejecuta la auditoría completa de Sentinel AI y resume sondas, anomalías y acciones pendientes.',
          );
        } else if (action === 'security') {
          await sendAi(
            'Genera el reporte de seguridad: Pi-hole, DNSSEC de Unbound y CrowdSec IPS.',
          );
        } else if (action === 'media') {
          await sendAi(
            'Consulta descargas activas, colas de Radarr/Sonarr y actividad reciente de Jellyfin.',
          );
        } else if (action === 'restart') {
          await showRestartMenu(messageId);
        } else if (action === 'clear') {
          await clearAiMemory();
        } else if (action === 'back') {
          await showAiMenu(messageId);
        } else {
          await ack(query, 'Acción desconocida');
        }
        break;
      }

      case 'corp': {
        if (action === 'report') {
          await ack(query);
          await showCorpReport(messageId);
        } else if (action === 'proposals') {
          await ack(query);
          await showCorpProposals(messageId);
        } else if (action === 'director') {
          await ack(query, 'Consultando al Director…');
          await showCorpReport(
            messageId,
            'Ronda completa del Director con los 8 empleados; entrega reporte ejecutivo.',
          );
        } else if (action === 'apply') {
          await ack(query, 'Aplicando mejora…');
          await applyCorpProposal(argument, messageId);
        } else {
          await ack(query, 'Acción desconocida');
        }
        break;
      }

      case 'ai': {
        if (action === 'diagnose') {
          await ack(query, 'Diagnosticando con IA…');
          const topic = String(argument || '').replace(/_/g, ' ');
          await sendAi(
            `Diagnostica con IA el problema relacionado con "${topic}". Usa las herramientas disponibles y propón acciones seguras.`,
          );
          break;
        }
        if (action === 'restart_menu') {
          await ack(query);
          if (!config.restartWhitelist.includes(argument)) {
            await notify(`⛔ <b>${esc(argument || 'servicio')}</b> no está en la lista blanca.`);
            break;
          }
          await notify(`⚠️ ¿Reiniciar <b>${esc(argument)}</b>?`, {
            reply_markup: { inline_keyboard: ui.restartConfirmKeyboard(argument) },
          });
          break;
        }
        if (action === 'restart_yes') {
          await ack(query, 'Reiniciando…');
          if (!config.restartWhitelist.includes(argument)) {
            await notify('⛔ Servicio no permitido.');
            break;
          }
          await notify(`🔄 Reiniciando <b>${esc(argument)}</b>…`);
          const ok = await docker.restartContainer(argument);
          await sleep(4000);
          await notify(
            ok
              ? `✅ <b>${esc(argument)}</b> reiniciado.`
              : `❌ No pude reiniciar <b>${esc(argument)}</b>.`,
          );
          await clearButtons(messageId);
          break;
        }
        if (action === 'restart_cancel') {
          await ack(query, 'Cancelado');
          await clearButtons(messageId);
          break;
        }
        await ack(query, 'Acción desconocida');
        break;
      }

      default:
        await ack(query, 'Acción desconocida');
    }
  } catch (error) {
    logger.error(`[callback] ${error.stack || error.message}`);
    await ack(query, '⚠️ Error interno');
    await notify(`⚠️ Error en la acción: ${esc(error.message)}`);
  }
}

async function runSelfTest(includePanel = false) {
  const checks = [];
  const add = async (name, fn) => {
    const started = Date.now();
    try {
      const detail = await fn();
      checks.push({
        name,
        ok: true,
        detail: String(detail),
        ms: Date.now() - started,
      });
    } catch (error) {
      checks.push({
        name,
        ok: false,
        detail: error.message,
        ms: Date.now() - started,
      });
    }
  };

  await add('Telegram', async () => {
    const me = await bot.getMe();
    return `@${me.username}`;
  });
  await add('AI Brain', async () => {
    const response = await rawRequest(`${config.ai.brainUrl}/ping`, {
      timeout: 5000,
    });
    if (response.status !== 200 || !response.data?.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return `${response.data.model} · ${response.data.allowed} chat(es) autorizado(s)`;
  });
  await add('Sentinel AI', async () => {
    const state = await getJson(`${config.sentinel.url}/api/state`, {
      timeout: 10000,
    });
    return `${Object.keys(state.probes || {}).length} sondas · ${(state.anomalies || []).length} anomalías`;
  });
  await add('Docker', async () => {
    const list = await docker.listContainers();
    if (list.length === 0) throw new Error('sin respuesta del socket');
    return `${list.filter((entry) => entry.state === 'running').length}/${list.length} activos`;
  });
  await add('Host', async () => {
    const metrics = await host.collect({ force: true });
    return `${metrics.cpu.percent.toFixed(0)}% CPU · ${metrics.temperature ? `${metrics.temperature.celsius.toFixed(1)} °C` : 'temp n/d'} · ${formatBytes(metrics.disk.free)} libres`;
  });
  await add('Radarr', async () => (await radarr.status()).version);
  await add('Sonarr', async () => (await sonarr.status()).version);
  await add('Seerr', async () => `v${(await seerr.status()).version}`);
  await add('Jellyfin', async () => {
    const data = await jellyfin.iptv();
    if (!data.ok) throw new Error(data.error || 'sin respuesta');
    return `v${data.version} · ${data.channels} canales`;
  });
  await add('qBittorrent', async () => {
    const torrents = await qbittorrent.torrents();
    return `${torrents.length} torrents`;
  });
  await add('Pi-hole', async () => {
    const summary = await pihole.summary();
    return `${summary.queries.total} consultas · ${summary.queries.blocked} bloqueadas`;
  });
  await add('Nodo 2', async () => {
    const status = await node2.status();
    if (!status.online) return 'apagado (WoL listo)';
    if (!status.ssh) throw new Error(status.error || 'sin SSH');
    const cpu =
      status.cpuPercent == null ? 'CPU n/d' : `${status.cpuPercent.toFixed(0)}% CPU`;
    return `${status.hostname} · ${cpu} · ${formatBytes(status.memory?.used || 0)} RAM`;
  });

  await notify(telemetry.buildSelfTest(checks));
  const failed = checks.filter((check) => !check.ok);
  logger.info(
    `[selftest] ${checks.length - failed.length}/${checks.length} integraciones OK`,
  );
  for (const check of checks.filter((entry) => !entry.ok)) {
    logger.error(`[selftest] ${check.name}: ${check.detail}`);
  }
  if (includePanel) {
    await renderPanel(null, await getMainView());
  }
  if (failed.length > 0) process.exitCode = 1;
}

function onPollingError(error) {
  const message = `${error.code || ''} ${error.message}`.trim();
  if (!onPollingError.lastLog || Date.now() - onPollingError.lastLog > 60000) {
    onPollingError.lastLog = Date.now();
    logger.warn(`[telegram] polling: ${message}`);
  }
  onPollingError.count = (onPollingError.count || 0) + 1;
  // El polling de la librería reintenta solo; si falla de forma sostenida
  // (DNS/Tailscale caído), reiniciamos el bucle de forma controlada.
  if (onPollingError.count >= 5 && !onPollingError.restarting) {
    onPollingError.restarting = true;
    logger.warn('[telegram] 5 errores de polling consecutivos; reiniciando bucle en 30 s');
    setTimeout(async () => {
      try {
        await bot.stopPolling({ cancel: true });
      } catch (stopError) {
        logger.debug(`[telegram] stopPolling: ${stopError.message}`);
      }
      try {
        await bot.startPolling({ interval: 1200, params: { timeout: 15 } });
        logger.info('[telegram] polling reiniciado correctamente');
      } catch (startError) {
        logger.error(`[telegram] startPolling: ${startError.message}`);
      }
      onPollingError.count = 0;
      onPollingError.restarting = false;
    }, 30000);
  }
}

async function shutdown(signal) {
  logger.info(`[bot] recibido ${signal}; apagando…`);
  scheduler.stop();
  store.flush();
  try {
    await bot.stopPolling({ cancel: true });
  } catch (error) {
    logger.debug(`[bot] stopPolling: ${error.message}`);
  }
  process.exit(0);
}

async function start() {
  try {
    await bot.setMyCommands(COMMANDS);
  } catch (error) {
    logger.warn(`[telegram] setMyCommands: ${error.message}`);
  }

  bot.on('message', onMessage);
  bot.on('callback_query', onCallback);
  bot.on('polling_error', onPollingError);

  process.on('unhandledRejection', (error) => {
    logger.error(`[process] unhandledRejection: ${error?.stack || error}`);
  });
  process.on('uncaughtException', (error) => {
    logger.error(`[process] uncaughtException: ${error?.stack || error}`);
    process.exit(1);
  });
  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));

  scheduler.start();
  logger.info(
    `[bot] Optiplex Brain ${config.version} online; chat autorizado ${config.telegram.chatId}`,
  );

  if (config.startupMessage) {
    setTimeout(async () => {
      try {
        await renderPanel(null, await getMainView({ startup: true }));
      } catch (error) {
        logger.error(`[bot] panel de arranque: ${error.message}`);
      }
    }, 3000);
  }
}

const selfHeal = new SelfHeal({ notify });
const storage = new StorageGuardian({ notify });
const eventBus = new EventBus({ notify });
const security = new SecurityWatcher({ notify });
const sentinelBridge = new SentinelBridge({ notify });
const scheduler = new Scheduler({
  notify,
  selfHeal,
  storage,
  eventBus,
  security,
  sentinelBridge,
});

if (SELFTEST) {
  runSelfTest(true)
    .then(() => {
      store.flush();
      process.exit(process.exitCode || 0);
    })
    .catch((error) => {
      logger.error(`[selftest] ${error.stack || error.message}`);
      process.exit(1);
    });
} else {
  start();
}
