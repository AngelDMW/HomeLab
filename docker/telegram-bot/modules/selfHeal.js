'use strict';

const config = require('../config');
const logger = require('../lib/logger');
const store = require('../lib/store');
const docker = require('./dockerClient');
const { qbittorrent } = require('./qbittorrent');
const { escapeHtml: esc, formatDuration, sleep } = require('../lib/format');
const { tcpProbe } = require('../lib/http');

class SelfHeal {
  constructor({ notify }) {
    this.notify = notify;
  }

  async checkContainers() {
    const containers = await docker.listContainers();
    if (containers.length === 0) {
      logger.warn('[self-heal] Docker no devolvió contenedores; se omite el ciclo');
      return;
    }

    const byName = new Map(containers.map((container) => [container.name, container]));
    const incidents = store.get('incidents', {});
    const now = Date.now();

    for (const expected of config.expectedContainers) {
      const container = byName.get(expected.name);
      const incident = incidents[expected.name];

      if (!container) {
        if (!incident?.missing) {
          incidents[expected.name] = {
            ...(incident || {}),
            missing: true,
            at: now,
          };
          await this.notify(
            `⚪ <b>${esc(expected.label)}</b> no aparece en Docker.\nRevisa <code>docker ps -a</code> en el host.`,
          );
        }
        continue;
      }

      const unhealthy = container.health === 'unhealthy';
      const operational = container.state === 'running' && !unhealthy;

      if (operational) {
        if (incident && (incident.failed || incident.missing || incident.attempts > 0)) {
          await this.notify(
            `✅ <b>${esc(expected.label)}</b> está operativo de nuevo.\nEstado: ${esc(container.status || container.state)}`,
          );
          delete incidents[expected.name];
        }
        continue;
      }

      if (expected.name === config.selfName) continue;

      if (incident?.failed) {
        if (now - incident.at < 30 * 60 * 1000) continue;
        incident.attempts = 0;
        incident.failed = false;
      }

      if (incident?.lastAttemptAt && now - incident.lastAttemptAt < 5 * 60 * 1000) {
        continue;
      }

      await this.recover(expected, container, incidents);
    }

    store.set('incidents', incidents);
  }

  async recover(expected, container, incidents) {
    const maxAttempts = config.thresholds.restartMax;
    let attempts = Number(incidents[expected.name]?.attempts) || 0;
    const started = Date.now();

    for (let attempt = attempts + 1; attempt <= maxAttempts; attempt += 1) {
      await this.notify(
        `🛠️ <b>Autorreparación</b>\n<b>${esc(expected.label)}</b> no está sano (${esc(container.status || container.state)}).\nIntento <b>${attempt}/${maxAttempts}</b>…`,
      );
      const restarted = await docker.restartContainer(expected.name);
      if (!restarted) {
        await this.notify(
          `⚠️ No pude reiniciar <b>${esc(expected.label)}</b> (el socket de Docker rechazó la orden).`,
        );
        incidents[expected.name] = {
          attempts: attempt,
          lastAttemptAt: Date.now(),
          failed: true,
          at: Date.now(),
        };
        return;
      }

      await sleep(config.thresholds.restartWaitMs);
      const info = await docker.inspectContainer(expected.name);
      const state = info?.State || {};
      const health = state.Health?.Status;
      const portOk = expected.port
        ? await tcpProbe('127.0.0.1', expected.port, 4000)
            .then(() => true)
            .catch(() => false)
        : true;

      const recovered =
        state.Running === true && health !== 'unhealthy' && portOk;

      incidents[expected.name] = {
        attempts: attempt,
        lastAttemptAt: Date.now(),
        failed: false,
        at: Date.now(),
      };

      if (recovered) {
        const downtime = Math.round((Date.now() - started) / 1000);
        await this.notify(
          `✅ <b>${esc(expected.label)}</b> recuperado en ${formatDuration(downtime)} (intento ${attempt}).\nEstado: ${esc(health || (state.Running ? 'running' : 'unknown'))}${expected.port ? ` · puerto ${expected.port} ${portOk ? 'OK' : 'sin respuesta'}` : ''}`,
        );
        return;
      }
    }

    const logs = await docker.getLogs(expected.name, 15);
    incidents[expected.name] = {
      attempts: maxAttempts,
      lastAttemptAt: Date.now(),
      failed: true,
      at: Date.now(),
    };
    const detail = logs ? `<pre>${esc(logs).slice(-3000)}</pre>` : '<i>Sin logs disponibles.</i>';
    await this.notify(
      `🚨 <b>FALLO PERSISTENTE: ${esc(expected.label)}</b>\nNo se recuperó tras ${maxAttempts} intentos.\n\n${detail}`,
    );
  }

  async checkStalledTorrents() {
    let torrents;
    try {
      torrents = await qbittorrent.torrents();
    } catch (error) {
      logger.warn(`[self-heal] qBittorrent no disponible: ${error.message}`);
      return;
    }

    const stalled = store.get('stalledTorrents', {});
    const now = Date.now() / 1000;
    const threshold = config.thresholds.stalledHours * 3600;
    const present = new Set();

    for (const torrent of torrents) {
      if (torrent.state !== 'stalledDL') continue;
      if (Number(torrent.num_seeds) > 0) continue;
      const idle = now - Number(torrent.last_activity || torrent.added_on || now);
      if (idle < threshold) continue;

      present.add(torrent.hash);
      if (stalled[torrent.hash]) continue;
      stalled[torrent.hash] = { at: Date.now() };

      await this.notify(
        `🧲 <b>Torrent estancado</b>\n<b>${esc(torrent.name)}</b>\nSin seeds desde hace ${formatDuration(idle)} · ${(Number(torrent.progress) * 100).toFixed(0)}% completado`,
        {
          reply_markup: {
            inline_keyboard: [
              [
                {
                  text: '🔄 Buscar otro release',
                  callback_data: `stall:search:${torrent.hash}`,
                },
              ],
              [
                {
                  text: '🗑️ Eliminar torrent',
                  callback_data: `stall:delete:${torrent.hash}`,
                },
                { text: '🙈 Ignorar', callback_data: `stall:ignore:${torrent.hash}` },
              ],
            ],
          },
        },
      );
    }

    for (const hash of Object.keys(stalled)) {
      if (!present.has(hash)) delete stalled[hash];
    }
    store.set('stalledTorrents', stalled);
  }
}

module.exports = SelfHeal;
