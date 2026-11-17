'use strict';

const config = require('../config');
const { rawRequest } = require('../lib/http');
const { lookupTitle } = require('../lib/format');

class ArrClient {
  constructor(options) {
    this.url = options.url;
    this.apiKey = options.apiKey;
    this.label = options.label;
    this.kind = options.kind;
    this.category = options.category;
  }

  async api(pathname, options = {}) {
    const { method = 'GET', body = null, timeout = 12000 } = options;
    const response = await rawRequest(`${this.url}${pathname}`, {
      method,
      headers: { 'X-Api-Key': this.apiKey },
      body,
      timeout,
    });
    if (response.status < 200 || response.status >= 300) {
      const detail =
        typeof response.data === 'string' ? response.data.slice(0, 120) : '';
      throw new Error(`${this.label} HTTP ${response.status} ${detail}`.trim());
    }
    return response.data;
  }

  async status() {
    return this.api('/api/v3/system/status');
  }

  async queue() {
    const data = await this.api('/api/v3/queue?page=1&pageSize=50');
    return Array.isArray(data?.records) ? data.records : [];
  }

  async historySince(hours = 24, eventType = 'downloadFolderImported') {
    const since = new Date(Date.now() - hours * 3600 * 1000).toISOString();
    const suffix = eventType ? `&eventType=${encodeURIComponent(eventType)}` : '';
    const data = await this.api(
      `/api/v3/history?page=1&pageSize=50&sortKey=date&sortDirection=desc&since=${encodeURIComponent(since)}${suffix}`,
    );
    return Array.isArray(data?.records) ? data.records : [];
  }

  async library() {
    const endpoint = this.kind === 'movie' ? '/api/v3/movie' : '/api/v3/series';
    const data = await this.api(endpoint);
    return Array.isArray(data) ? data : [];
  }

  async lookup(term) {
    const clean = String(term || '').trim();
    if (!clean) throw new Error('Término de búsqueda vacío');
    const type = this.kind === 'movie' ? 'movie' : 'series';
    const data = await this.api(
      `/api/v3/${type}/lookup?term=${encodeURIComponent(clean)}`,
    );
    return Array.isArray(data) ? data : [];
  }

  async searchAlternative(title) {
    const term = lookupTitle(title);
    const results = await this.lookup(term);
    const existing = results.find((entry) => Number.isInteger(entry.id) && entry.id > 0);
    if (!existing) {
      return {
        ok: false,
        message: `No encontré “${term}” dentro de la biblioteca de ${this.label}.`,
      };
    }

    if (this.kind === 'movie') {
      await this.api('/api/v3/command', {
        method: 'POST',
        body: { name: 'MoviesSearch', movieIds: [existing.id] },
      });
    } else {
      await this.api('/api/v3/command', {
        method: 'POST',
        body: { name: 'SeriesSearch', seriesId: existing.id },
      });
    }

    return {
      ok: true,
      title: existing.title || existing.name || term,
      service: this.label,
    };
  }
}

const radarr = new ArrClient(config.radarr);
const sonarr = new ArrClient(config.sonarr);

module.exports = { ArrClient, radarr, sonarr };
