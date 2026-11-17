'use strict';

const config = require('../config');
const { rawRequest } = require('../lib/http');

class JellyfinClient {
  constructor(options) {
    this.url = options.url;
    this.apiKey = options.apiKey;
    this.publicUrl = options.publicUrl;
    this.headers = {
      Authorization: `MediaBrowser Token="${this.apiKey}"`,
    };
  }

  async api(pathname, options = {}) {
    const { timeout = 10000 } = options;
    const response = await rawRequest(`${this.url}${pathname}`, {
      headers: this.headers,
      timeout,
    });
    if (response.status < 200 || response.status >= 300) {
      throw new Error(`Jellyfin HTTP ${response.status}`);
    }
    return response.data;
  }

  async status() {
    return this.api('/System/Info');
  }

  async sessions() {
    const data = await this.api('/Sessions');
    return Array.isArray(data) ? data : [];
  }

  async activeSessions() {
    const sessions = await this.sessions();
    return sessions
      .filter((session) => session.NowPlayingItem)
      .map((session) => ({
        user: session.UserName || '—',
        client: session.Client || session.DeviceName || '—',
        title:
          session.NowPlayingItem.Name ||
          session.NowPlayingItem.SeriesName ||
          'Reproduciendo',
      }));
  }

  async iptv() {
    const info = await this.status().catch(() => null);
    let channels = 0;
    let epg = false;
    let error = null;

    try {
      const result = await this.api('/LiveTv/Channels?limit=1');
      channels = Number(
        result?.TotalRecordCount || (Array.isArray(result?.Items) ? result.Items.length : 0),
      );
      const programs = await this.api('/LiveTv/Programs?limit=1').catch(() => null);
      epg = Boolean(
        programs &&
          (Number(programs.TotalRecordCount) > 0 ||
            (Array.isArray(programs.Items) && programs.Items.length > 0)),
      );
    } catch (caught) {
      error = caught.message;
    }

    return {
      ok: !error,
      version: info?.Version || '—',
      channels,
      epg,
      error,
      url: this.publicUrl,
    };
  }
}

module.exports = {
  JellyfinClient,
  jellyfin: new JellyfinClient(config.jellyfin),
};
