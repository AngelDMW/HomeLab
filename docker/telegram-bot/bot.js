'use strict';

const TelegramBot = require('node-telegram-bot-api');
const config = require('./config');
const logger = require('./lib/logger');
const store = require('./lib/store');
const { sleep, escapeHtml: esc, formatBytes } = require('./lib/format');
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

function isOwnerChat(chatId) {
  return Number(chatId) === Number(config.telegram.chatId);
}

function guard(message) {
  if (message?.chat && isOwnerChat(message.chat.id)) return true;
  logger.warn(
    `[security] mensaje no autorizado chat=${message?.chat?.id} user=${message?.from?.username || message?.from?.id}`,
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
  if (!service) {
    return notify(
      'No reconozco ese servicio. Usa /containers para ver la lista disponible.',
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
    default:
      return showMain(messageId);
  }
}

const COMMANDS = [
  { command: 'mission', description: 'Panel de control principal' },
  { command: 'status', description: 'Estado general del servidor' },
  { command: 'telemetry', description: 'Telemetría en vivo' },
  { command: 'containers', description: 'Estado y reinicio de contenedores' },
  { command: 'queue', description: 'Cola de descargas y solicitudes' },
  { command: 'buscar', description: 'Buscar y solicitar una película' },
  { command: 'briefing', description: 'Reporte ejecutivo ahora' },
  { command: 'iptv', description: 'Salud de IPTV en Jellyfin' },
  { command: 'disk', description: 'Detalle de almacenamiento' },
  { command: 'maint', description: 'Mantenimiento y limpieza' },
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
    case 'status':
    case 'menu':
    case 'telemetry':
      return showMain();
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

  const answer = await ai.chat(text, context);
  if (answer) return sendPlain(answer);
  return notify('🤖 No entendí la orden. Usa /help o el panel /mission.');
}

async function onMessage(message) {
  if (!guard(message)) return;
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
  if (!query.message?.chat || !isOwnerChat(query.message.chat.id)) {
    await ack(query, 'No autorizado');
    logger.warn(`[security] callback no autorizado chat=${query.message?.chat?.id}`);
    return;
  }

  const [scope, action, argument] = String(query.data || '').split(':');
  try {
    switch (scope) {
      case 'view':
        await ack(query);
        await openView(action, messageId);
        break;

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
  if (!onPollingError.lastLog || Date.now() - onPollingError.lastLog > 60000) {
    onPollingError.lastLog = Date.now();
    logger.warn(
      `[telegram] polling: ${error.code || ''} ${error.message}`.trim(),
    );
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
const scheduler = new Scheduler({ notify, selfHeal, storage, eventBus, security });

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
