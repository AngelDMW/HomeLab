'use strict';

const config = require('../config');
const { rawRequest } = require('../lib/http');

class QbittorrentClient {
  constructor(options) {
    this.url = options.url;
    this.username = options.username;
    this.password = options.password;
    this.cookie = '';
  }

  async login(force = false) {
    if (this.cookie && !force) return;
    const form = new URLSearchParams({
      username: this.username,
      password: this.password,
    }).toString();
    const response = await rawRequest(`${this.url}/api/v2/auth/login`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        Referer: this.url,
      },
      body: form,
      timeout: 8000,
    });
    if (response.status >= 400) {
      throw new Error(`qBittorrent login HTTP ${response.status}`);
    }
    const setCookie = response.headers['set-cookie'];
    if (Array.isArray(setCookie) && setCookie.length > 0) {
      this.cookie = setCookie.map((entry) => entry.split(';')[0]).join('; ');
    }
    const text = String(response.data || '').trim();
    if (text.startsWith('Fails')) {
      throw new Error('qBittorrent rechazó las credenciales');
    }
  }

  async api(pathname, options = {}) {
    const { method = 'GET', form = null, retry = true } = options;
    await this.login();
    const headers = { Referer: this.url, Origin: this.url };
    if (this.cookie) headers.Cookie = this.cookie;
    if (form) headers['Content-Type'] = 'application/x-www-form-urlencoded';

    const response = await rawRequest(`${this.url}${pathname}`, {
      method,
      headers,
      body: form,
      timeout: 12000,
    });

    if (response.status === 403 && retry) {
      this.cookie = '';
      await this.login(true);
      return this.api(pathname, { ...options, retry: false });
    }
    if (response.status >= 400) {
      const detail = String(response.data || '').trim().slice(0, 120);
      throw new Error(`qBittorrent HTTP ${response.status} ${detail}`.trim());
    }
    const text = String(response.data || '').trim();
    if (text.startsWith('Fails')) {
      throw new Error('qBittorrent rechazó la operación');
    }
    return response.data;
  }

  async torrents() {
    const data = await this.api('/api/v2/torrents/info');
    return Array.isArray(data) ? data : [];
  }

  async torrentsAction(action, hashes = 'all') {
    const form = new URLSearchParams({ hashes }).toString();
    try {
      await this.api(`/api/v2/torrents/${action}`, { method: 'POST', form });
    } catch (error) {
      if (!/HTTP 404/.test(error.message)) throw error;
      const legacy = action === 'stop' ? 'pause' : 'resume';
      await this.api(
        `/api/v2/torrents/${legacy}?hashes=${encodeURIComponent(hashes)}`,
        { method: 'POST' },
      );
    }
  }

  async pauseAll() {
    await this.torrentsAction('stop', 'all');
  }

  async resumeAll() {
    await this.torrentsAction('start', 'all');
  }

  async deleteTorrent(hash, deleteFiles = false) {
    const form = new URLSearchParams({
      hashes: hash,
      deleteFiles: deleteFiles ? 'true' : 'false',
    }).toString();
    await this.api('/api/v2/torrents/delete', { method: 'POST', form });
  }
}

module.exports = {
  QbittorrentClient,
  qbittorrent: new QbittorrentClient(config.qbittorrent),
};
