'use strict';

const config = require('../config');
const { rawRequest } = require('../lib/http');

class PiholeClient {
  constructor(options) {
    this.url = options.url;
    this.password = options.password;
    this.sid = '';
  }

  async auth(force = false) {
    if (this.sid && !force) return;
    const response = await rawRequest(`${this.url}/api/auth`, {
      method: 'POST',
      body: { password: this.password },
      timeout: 8000,
    });
    if (
      response.status !== 200 ||
      !response.data ||
      !response.data.session ||
      response.data.session.valid !== true
    ) {
      throw new Error(`Pi-hole auth HTTP ${response.status}`);
    }
    this.sid = response.data.session.sid;
  }

  async api(pathname) {
    await this.auth();
    let response = await rawRequest(`${this.url}${pathname}`, {
      headers: { 'X-FTL-SID': this.sid },
      timeout: 10000,
    });
    if (response.status === 401) {
      await this.auth(true);
      response = await rawRequest(`${this.url}${pathname}`, {
        headers: { 'X-FTL-SID': this.sid },
        timeout: 10000,
      });
    }
    if (response.status >= 400) {
      throw new Error(`Pi-hole HTTP ${response.status}`);
    }
    return response.data;
  }

  async summary() {
    const data = await this.api('/api/stats/summary');
    return {
      queries: {
        total: Number(data?.queries?.total) || 0,
        blocked: Number(data?.queries?.blocked) || 0,
        percentBlocked: Number(data?.queries?.percent_blocked) || 0,
      },
      domainsBlocked: Number(data?.gravity?.domains_being_blocked) || 0,
    };
  }

  async topBlocked() {
    const data = await this.api('/api/stats/top_domains?blocked=true&count=5');
    return Array.isArray(data?.domains) ? data.domains : [];
  }
}

module.exports = { PiholeClient, pihole: new PiholeClient(config.pihole) };
