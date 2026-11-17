'use strict';

const { execFile } = require('child_process');
const { promisify } = require('util');
const path = require('path');
const config = require('../config');
const logger = require('../lib/logger');
const store = require('../lib/store');
const docker = require('./dockerClient');
const host = require('./hostMetrics');
const telemetry = require('./telemetry');
const ui = require('./ui');
const { qbittorrent } = require('./qbittorrent');
const { formatBytes, escapeHtml: esc } = require('../lib/format');

const execFileAsync = promisify(execFile);

class StorageGuardian {
  constructor({ notify }) {
    this.notify = notify;
    this.breakdownCache = { at: 0, data: [] };
  }

  async duBytes(target) {
    try {
      const { stdout } = await execFileAsync('du', ['-sx', '-B1', target], {
        timeout: 30000,
        maxBuffer: 1024 * 1024,
      });
      const value = Number.parseInt(stdout.trim().split(/\s+/)[0], 10);
      return Number.isFinite(value) ? value : null;
    } catch (error) {
      logger.warn(`[storage] du ${target}: ${error.message}`);
      return null;
    }
  }

  async breakdown(force = false) {
    if (
      !force &&
      this.breakdownCache.data.length > 0 &&
      Date.now() - this.breakdownCache.at < 10 * 60 * 1000
    ) {
      return this.breakdownCache.data;
    }

    const entries = [];
    const mediaPaths = [
      { label: 'Películas', key: config.paths.movies },
      { label: 'Series', key: config.paths.series },
      { label: 'Descargas', key: config.paths.downloads },
    ];
    for (const item of mediaPaths) {
      const size = await this.duBytes(path.join(config.hostRoot, item.key));
      if (size !== null) entries.push({ label: item.label, bytes: size });
    }

    let df = null;
    try {
      df = await docker.systemDf();
    } catch (error) {
      logger.warn(`[storage] docker df: ${error.message}`);
    }

    if (df) {
      const volumes = df.Volumes || [];
      const nextcloud = volumes.find(
        (volume) => volume.Name === 'nextcloud_nextcloud_data',
      );
      const nextcloudBytes = Number(nextcloud?.UsageData?.Size) || 0;
      const volumeTotal = volumes.reduce(
        (sum, volume) => sum + (Number(volume.UsageData?.Size) || 0),
        0,
      );
      const images = (df.Images || []).reduce(
        (sum, entry) => sum + (Number(entry.Size) || 0),
        0,
      );
      const containers = (df.Containers || []).reduce(
        (sum, entry) => sum + (Number(entry.SizeRw) || 0),
        0,
      );
      const buildCache = (df.BuildCache || []).reduce(
        (sum, entry) => sum + (Number(entry.Size) || 0),
        0,
      );
      if (nextcloudBytes > 0) {
        entries.push({ label: 'Nextcloud (volumen)', bytes: nextcloudBytes });
      }
      const dockerOther = images + containers + buildCache + (volumeTotal - nextcloudBytes);
      entries.push({
        label: 'Docker (imágenes, caché y otros volúmenes)',
        bytes: Math.max(0, dockerOther),
      });
    }

    entries.sort((a, b) => b.bytes - a.bytes);
    this.breakdownCache = { at: Date.now(), data: entries };
    return entries;
  }

  async check() {
    const metrics = await host.collect({ force: true, includeNetwork: false });
    const freeGB = metrics.disk.freeGB;
    const previous = store.get('storage', { level: 'ok', autoPaused: false });
    let autoPaused = Boolean(previous.autoPaused);
    let resumed = false;

    if (autoPaused && freeGB >= config.thresholds.diskResumeGB) {
      try {
        await qbittorrent.resumeAll();
        autoPaused = false;
        resumed = true;
      } catch (error) {
        logger.warn(`[storage] no se pudo reanudar qBittorrent: ${error.message}`);
      }
    }

    let level = 'ok';
    if (freeGB < config.thresholds.diskCriticalGB) level = 'critical';
    else if (freeGB < config.thresholds.diskWarnGB) level = 'warning';

    if (level === previous.level) {
      if (autoPaused !== previous.autoPaused || resumed) {
        store.set('storage', { ...previous, autoPaused, freeGB, at: Date.now() });
      }
      return;
    }

    if (level === 'critical') {
      let paused = autoPaused;
      if (!paused) {
        try {
          await qbittorrent.pauseAll();
          paused = true;
        } catch (error) {
          logger.warn(`[storage] no se pudo pausar qBittorrent: ${error.message}`);
        }
      }
      const breakdown = await this.breakdown();
      const text = `${telemetry.buildStorageAlert({ metrics, breakdown, level })}\n\n${paused ? '⏸️ Descargas pausadas automáticamente.' : '⚠️ No pude pausar qBittorrent.'}`;
      await this.notify(text, {
        reply_markup: { inline_keyboard: ui.storageAlertKeyboard() },
      });
      store.set('storage', {
        level,
        autoPaused: paused,
        freeGB,
        at: Date.now(),
      });
      return;
    }

    if (level === 'warning') {
      const breakdown = await this.breakdown();
      const recoveredFromCritical = previous.level === 'critical';
      const header = recoveredFromCritical
        ? '✅ <b>ALMACENAMIENTO ESTABILIZADO</b>'
        : null;
      const body = telemetry.buildStorageAlert({ metrics, breakdown, level });
      const resumeNote = resumed ? '\n▶️ Descargas reanudadas automáticamente.' : '';
      await this.notify(`${header ? `${header}\n` : ''}${body}${resumeNote}`, {
        reply_markup: { inline_keyboard: ui.storageAlertKeyboard() },
      });
      store.set('storage', {
        level,
        autoPaused,
        freeGB,
        at: Date.now(),
      });
      return;
    }

    if (previous.level !== 'ok') {
      await this.notify(
        `✅ <b>Almacenamiento recuperado</b>\nLibre: <b>${formatBytes(metrics.disk.free)}</b> (${metrics.disk.percent.toFixed(0)}% usado).${resumed ? '\n▶️ Descargas reanudadas.' : ''}${autoPaused ? '\n⏸️ Descargas aún pausadas; se reanudarán al superar ' + config.thresholds.diskResumeGB + ' GB.' : ''}`,
      );
    }
    store.set('storage', { level: 'ok', autoPaused, freeGB, at: Date.now() });
  }
}

module.exports = StorageGuardian;
