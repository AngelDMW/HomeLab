'use strict';

const config = require('../config');
const { rawRequest } = require('../lib/http');

const STATUS_LABELS = {
  1: 'Pendiente',
  2: 'Aprobada',
  3: 'Rechazada',
  4: 'Fallida',
  5: 'Disponible',
};

const MEDIA_STATUS_LABELS = {
  1: 'No solicitada',
  2: 'Pendiente',
  3: 'Procesando',
  4: 'Parcialmente disponible',
  5: 'Disponible en Jellyfin',
};

class SeerrClient {
  constructor(options) {
    this.url = options.url;
    this.apiKey = options.apiKey;
    this.publicUrl = options.publicUrl;
    this.titleCache = new Map();
  }

  async api(pathname, options = {}) {
    const { timeout = 10000, method = 'GET', body = null } = options;
    const response = await rawRequest(`${this.url}${pathname}`, {
      method,
      headers: { 'X-Api-Key': this.apiKey },
      body,
      timeout,
    });
    if (response.status < 200 || response.status >= 300) {
      const detail =
        typeof response.data === 'string' ? response.data.slice(0, 160) : '';
      throw new Error(`Seerr HTTP ${response.status} ${detail}`.trim());
    }
    return response.data;
  }

  async status() {
    return this.api('/api/v1/status');
  }

  async requests(options = {}) {
    const { take = 25 } = options;
    const data = await this.api(
      `/api/v1/request?take=${take}&skip=0&filter=all&sort=added`,
    );
    return Array.isArray(data?.results) ? data.results : [];
  }

  async mediaTitle(request) {
    const media = request?.media || {};
    const tmdbId = media.tmdbId;
    const type = request?.type === 'movie' ? 'movie' : 'tv';
    const key = `${type}:${tmdbId}`;
    if (this.titleCache.has(key)) return this.titleCache.get(key);
    if (!tmdbId) return `Solicitud #${request?.id || '?'}`;

    try {
      const data = await this.api(`/api/v1/${type}/${tmdbId}`);
      const title = data?.title || data?.name || `TMDB ${tmdbId}`;
      this.titleCache.set(key, title);
      return title;
    } catch (error) {
      return `TMDB ${tmdbId}`;
    }
  }

  async requestsWithTitles(options = {}) {
    const { take = 8 } = options;
    const requests = await this.requests({ take });
    const output = [];
    for (const request of requests) {
      const title = await this.mediaTitle(request);
      output.push({
        id: request.id,
        type: request.type,
        status: request.status,
        statusLabel: STATUS_LABELS[request.status] || `Estado ${request.status}`,
        title,
        requester: request.requestedBy?.displayName || 'Desconocido',
        createdAt: request.createdAt,
      });
    }
    return output;
  }

  statusLabel(status) {
    return STATUS_LABELS[status] || `Estado ${status}`;
  }

  mediaStatusLabel(status) {
    return MEDIA_STATUS_LABELS[status] || 'No solicitada';
  }

  async searchMovies(term) {
    const query = encodeURIComponent(String(term || '').trim());
    if (!query) throw new Error('Término de búsqueda vacío');
    const data = await this.api(`/api/v1/search?query=${query}&language=es`);
    return (Array.isArray(data?.results) ? data.results : [])
      .filter((item) => item.mediaType === 'movie' && item.title)
      .slice(0, 6)
      .map((item) => ({
        id: item.id,
        title: item.title,
        year: item.releaseDate ? String(item.releaseDate).slice(0, 4) : '',
        status: Number(item.mediaInfo?.status) || 0,
        statusLabel: this.mediaStatusLabel(Number(item.mediaInfo?.status) || 0),
      }));
  }

  async mediaDetails(type, id) {
    return this.api(`/api/v1/${type}/${id}`);
  }

  async createRequest(mediaType, mediaId) {
    return this.api('/api/v1/request', {
      method: 'POST',
      body: { mediaType, mediaId },
    });
  }
}

module.exports = {
  SeerrClient,
  seerr: new SeerrClient(config.seerr),
  STATUS_LABELS,
  MEDIA_STATUS_LABELS,
};
