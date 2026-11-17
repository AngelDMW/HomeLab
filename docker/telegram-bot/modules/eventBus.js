'use strict';

const config = require('../config');
const logger = require('../lib/logger');
const store = require('../lib/store');
const { qbittorrent } = require('./qbittorrent');
const { seerr } = require('./seerr');
const host = require('./hostMetrics');
const {
  formatBytes,
  formatDuration,
  progressBar,
  escapeHtml: esc,
  detectLanguage,
  cleanTitle,
} = require('../lib/format');

function normalizeTitle(value) {
  return String(value || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/\bS\d{1,2}E\d{1,2}\b/g, ' ')
    .replace(/\b(19|20)\d{2}\b/g, ' ')
    .replace(
      /\b(2160p|1080p|720p|480p|web|webdl|webrip|bluray|brrip|hdtv|x264|x265|hevc|aac|dts|repack|proper|multi|dual|latino|castellano|spanish)\b/g,
      ' ',
    )
    .replace(/[^a-z0-9]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function titlesMatch(left, right) {
  const a = normalizeTitle(left);
  const b = normalizeTitle(right);
  if (!a || !b) return false;
  if (a === b || a.includes(b) || b.includes(a)) return true;
  const wordsA = a.split(' ').filter((word) => word.length > 2);
  const wordsB = b.split(' ').filter((word) => word.length > 2);
  if (wordsA.length === 0 || wordsB.length === 0) return false;
  const overlap = wordsA.filter((word) => wordsB.includes(word)).length;
  return overlap / Math.min(wordsA.length, wordsB.length) >= 0.6;
}

class EventBus {
  constructor({ notify }) {
    this.notify = notify;
    this.requestCache = { at: 0, list: [] };
  }

  async getRequests(force = false) {
    if (!force && Date.now() - this.requestCache.at < 5 * 60 * 1000) {
      return this.requestCache.list;
    }
    const list = await seerr.requestsWithTitles({ take: 25 });
    this.requestCache = { at: Date.now(), list };
    return list;
  }

  findRequest(title, requests) {
    return requests.find((request) => titlesMatch(title, request.title));
  }

  async seed() {
    const [torrents, requests, network] = await Promise.all([
      qbittorrent.torrents().catch(() => null),
      this.getRequests(true).catch(() => null),
      host.networkStatus().catch(() => null),
    ]);

    if (Array.isArray(torrents)) {
      const state = {};
      for (const torrent of torrents) {
        const progress = Number(torrent.progress) || 0;
        state[torrent.hash] = {
          progress,
          startedNotified: true,
          doneNotified: progress >= 1,
        };
      }
      store.set('torrentsState', state);
    }

    if (Array.isArray(requests)) {
      store.set(
        'seerrSeen',
        requests.map((request) => request.id),
      );
    }

    store.set('connectivity', {
      internetDownAlerted: false,
      tailscaleDownAlerted: false,
      internetFails: 0,
      tailscaleFails: 0,
      at: Date.now(),
    });
    store.set('eventsInit', true);
    logger.info(
      `[events] estado inicial sembrado: ${torrents?.length || 0} torrents, ${requests?.length || 0} solicitudes, red ${network?.internet?.ok ? 'OK' : 'sin datos'}`,
    );
  }

  async poll() {
    if (!store.get('eventsInit', false)) {
      await this.seed();
      return;
    }
    await Promise.allSettled([
      this.pollTorrents(),
      this.pollSeerr(),
      this.pollConnectivity(),
    ]);
  }

  async pollTorrents() {
    let torrents;
    try {
      torrents = await qbittorrent.torrents();
    } catch (error) {
      logger.warn(`[events] qBittorrent: ${error.message}`);
      return;
    }
    let requests = [];
    try {
      requests = await this.getRequests();
    } catch (error) {
      logger.debug(`[events] Seerr para matching: ${error.message}`);
    }

    const state = store.get('torrentsState', {});
    const present = new Set();

    for (const torrent of torrents) {
      const hash = torrent.hash;
      present.add(hash);
      const progress = Number(torrent.progress) || 0;
      const previous = state[hash];

      if (!previous) {
        state[hash] = {
          progress,
          startedNotified: true,
          doneNotified: progress >= 1,
          addedAt: Number(torrent.added_on) || Math.floor(Date.now() / 1000),
        };

        if (progress < 1) {
          const request = this.findRequest(torrent.name, requests);
          const type = ['sonarr', 'tv'].includes(String(torrent.category))
            ? 'Serie'
            : 'Película';
          const eta =
            Number(torrent.eta) > 0 && Number(torrent.eta) < 7 * 86400
              ? `\nETA: <b>${formatDuration(torrent.eta)}</b>`
              : '';
          await this.notify(
            [
              '🎬 <b>Descarga iniciada</b>',
              `<b>${esc(cleanTitle(torrent.name))}</b>`,
              `Tipo: ${type} · Categoría: <code>${esc(torrent.category || '—')}</code>`,
              `Idioma detectado: <b>${detectLanguage(torrent.name)}</b>`,
              `Tamaño: <b>${formatBytes(torrent.size)}</b> · Seeds: ${torrent.num_seeds ?? 0}`,
              request
                ? `Solicitada por: <b>${esc(request.requester)}</b>`
                : null,
              `${progressBar(progress * 100)} ${(progress * 100).toFixed(0)}%${eta}`,
            ]
              .filter(Boolean)
              .join('\n'),
          );
        }
        continue;
      }

      previous.progress = progress;
      if (progress >= 1 && !previous.doneNotified) {
        previous.doneNotified = true;
        const request = this.findRequest(torrent.name, requests);
        const type = ['sonarr', 'tv'].includes(String(torrent.category))
          ? 'serie'
          : 'película';
        await this.notify(
          [
            '✅ <b>Descarga completada</b>',
            `<b>${esc(cleanTitle(torrent.name))}</b>`,
            `La ${type} terminó de descargarse (${formatBytes(torrent.size)}).`,
            request ? `Solicitada por <b>${esc(request.requester)}</b>.` : null,
            'Radarr/Sonarr la está importando; estará disponible en Jellyfin en breve.',
          ]
            .filter(Boolean)
            .join('\n'),
          {
            reply_markup: {
              inline_keyboard: [
                [{ text: '📺 Abrir Jellyfin', url: config.urls.jellyfin }],
              ],
            },
          },
        );
      }
    }

    for (const hash of Object.keys(state)) {
      if (!present.has(hash)) delete state[hash];
    }
    store.set('torrentsState', state);
  }

  async pollSeerr() {
    let requests;
    try {
      requests = await this.getRequests(true);
    } catch (error) {
      logger.warn(`[events] Seerr: ${error.message}`);
      return;
    }

    const seen = new Set(store.get('seerrSeen', []));
    const fresh = requests.filter((request) => !seen.has(request.id));
    for (const request of fresh.slice(0, 3)) {
      await this.notify(
        [
          '📥 <b>Nueva solicitud en Seerr</b>',
          `<b>${esc(request.title)}</b> · ${request.type === 'movie' ? 'Película' : 'Serie'}`,
          `Solicitada por <b>${esc(request.requester)}</b>`,
          `Estado: <b>${esc(request.statusLabel)}</b>`,
        ].join('\n'),
        {
          reply_markup: {
            inline_keyboard: [
              [{ text: '🔗 Abrir Seerr', url: config.urls.seerr }],
            ],
          },
        },
      );
    }

    const ids = requests.map((request) => request.id);
    for (const id of seen) {
      if (!ids.includes(id) && store.get('seerrSeen', []).length < 300) {
        ids.push(id);
      }
    }
    store.set('seerrSeen', ids.slice(-300));
  }

  async pollConnectivity() {
    const network = await host.networkStatus();
    const state = store.get('connectivity', {
      internetDownAlerted: false,
      tailscaleDownAlerted: false,
      internetFails: 0,
      tailscaleFails: 0,
    });

    state.internetFails = network.internet.ok ? 0 : (state.internetFails || 0) + 1;
    state.tailscaleFails = network.tailscale.ok
      ? 0
      : (state.tailscaleFails || 0) + 1;

    if (!network.internet.ok && !state.internetDownAlerted && state.internetFails >= 2) {
      state.internetDownAlerted = true;
      state.internetDownSince = Date.now();
      await this.notify(
        '🌐 <b>Internet caído</b>\nDos sondeos consecutivos fallaron. Revisa el router/ISP.',
      );
    } else if (network.internet.ok && state.internetDownAlerted) {
      const downtime = state.internetDownSince
        ? formatDuration((Date.now() - state.internetDownSince) / 1000)
        : '—';
      state.internetDownAlerted = false;
      state.internetDownSince = null;
      await this.notify(`✅ <b>Internet restablecido</b>\nCaída aproximada: ${downtime}.`);
    }

    if (
      !network.tailscale.ok &&
      !state.tailscaleDownAlerted &&
      state.tailscaleFails >= 2
    ) {
      state.tailscaleDownAlerted = true;
      state.tailscaleDownSince = Date.now();
      await this.notify(
        '🔗 <b>Tailscale sin enlace</b>\nNo hay respuesta de 100.100.100.100:53.',
      );
    } else if (network.tailscale.ok && state.tailscaleDownAlerted) {
      const downtime = state.tailscaleDownSince
        ? formatDuration((Date.now() - state.tailscaleDownSince) / 1000)
        : '—';
      state.tailscaleDownAlerted = false;
      state.tailscaleDownSince = null;
      await this.notify(
        `✅ <b>Tailscale reconectado</b>\nTiempo sin enlace: ${downtime}.`,
      );
    }

    state.at = Date.now();
    store.set('connectivity', state);
  }
}

module.exports = { EventBus, titlesMatch, normalizeTitle };
