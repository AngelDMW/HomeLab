'use strict';

const config = require('../config');
const logger = require('../lib/logger');
const store = require('../lib/store');
const docker = require('./dockerClient');
const host = require('./hostMetrics');
const telemetry = require('./telemetry');
const { radarr, sonarr } = require('./arr');
const { pihole } = require('./pihole');
const { dateKeyInTimezone } = require('../lib/format');

class Scheduler {
  constructor({ notify, selfHeal, storage, eventBus, security, sentinelBridge }) {
    this.notify = notify;
    this.selfHeal = selfHeal;
    this.storage = storage;
    this.eventBus = eventBus;
    this.security = security;
    this.sentinelBridge = sentinelBridge;
    this.timers = [];
  }

  every(intervalMs, fn, name, initialDelay = intervalMs) {
    let running = false;
    const run = async () => {
      if (running) {
        logger.warn(`[scheduler:${name}] ciclo anterior aún en curso; se omite`);
        return;
      }
      running = true;
      try {
        await fn();
      } catch (error) {
        logger.error(`[scheduler:${name}] ${error.stack || error.message}`);
      } finally {
        running = false;
      }
    };

    const initial = setTimeout(() => {
      run();
      const interval = setInterval(run, intervalMs);
      this.timers.push(interval);
    }, initialDelay);
    this.timers.push(initial);
  }

  start() {
    this.every(60 * 1000, () => this.selfHeal.checkContainers(), 'self-heal', 20000);
    this.every(60 * 1000, () => this.eventBus.poll(), 'event-bus', 35000);
    this.every(60 * 1000, () => this.security.check(), 'security', 75000);
    this.every(
      5 * 60 * 1000,
      () => this.sentinelBridge?.check(),
      'sentinel-bridge',
      120000,
    );
    this.every(5 * 60 * 1000, () => this.storage.check(), 'storage', 60000);
    this.every(
      10 * 60 * 1000,
      () => this.selfHeal.checkStalledTorrents(),
      'stalled',
      90000,
    );
    this.every(30 * 1000, () => this.checkBriefingWindow(), 'briefing', 8000);
    logger.info('[scheduler] vigilancia autónoma iniciada');
  }

  stop() {
    for (const timer of this.timers) {
      clearTimeout(timer);
      clearInterval(timer);
    }
    this.timers = [];
  }

  localTimeParts() {
    const parts = new Intl.DateTimeFormat('en-GB', {
      timeZone: config.timezone,
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).formatToParts(new Date());
    const read = (type) => Number(parts.find((part) => part.type === type)?.value);
    return { hour: read('hour'), minute: read('minute') };
  }

  async checkBriefingWindow() {
    const now = this.localTimeParts();
    const [targetHour, targetMinute] = config.briefing.time.split(':').map(Number);
    const currentMinutes = now.hour * 60 + now.minute;
    const targetMinutes = targetHour * 60 + targetMinute;
    const delta = currentMinutes - targetMinutes;
    if (delta < 0 || delta > config.briefing.catchUpMinutes) return;

    const today = dateKeyInTimezone(config.timezone);
    if (store.get('lastBriefing') === today) return;
    store.set('lastBriefing', today);
    await this.sendBriefing();
  }

  async buildBriefingText() {
    const [metrics, containers, radarrHistory, sonarrHistory, piholeSummary] =
      await Promise.all([
        host.collect({ force: true }),
        docker.listContainers().catch(() => []),
        radarr.historySince(24).catch(() => []),
        sonarr.historySince(24).catch(() => []),
        pihole.summary().catch(() => null),
      ]);

    const downloads = [
      ...radarrHistory.map((record) => ({
        title: record.movie?.title || record.sourceTitle || 'Película',
        type: 'Película',
      })),
      ...sonarrHistory.map((record) => ({
        title: record.series?.title || record.sourceTitle || 'Serie',
        type: 'Serie',
      })),
    ];

    return telemetry.buildBriefing({
      metrics,
      containers,
      downloads,
      pihole: piholeSummary,
    });
  }

  async sendBriefing() {
    const text = await this.buildBriefingText();
    await this.notify(text);
    logger.info('[briefing] reporte diario enviado');
  }
}

module.exports = Scheduler;
