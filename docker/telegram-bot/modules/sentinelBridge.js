'use strict';

const config = require('../config');
const logger = require('../lib/logger');
const store = require('../lib/store');
const { getJson } = require('../lib/http');
const { escapeHtml: esc } = require('../lib/format');

// Traduce el componente de Sentinel a un slug seguro para callback_data.
function slug(value) {
  return String(value || 'sistema')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 28) || 'sistema';
}

class SentinelBridge {
  constructor({ notify }) {
    this.notify = notify;
  }

  fingerprint(anomaly) {
    return [anomaly.component, anomaly.severity, anomaly.summary]
      .map((value) => String(value || ''))
      .join('|');
  }

  async check() {
    let state;
    try {
      state = await getJson(`${config.sentinel.url}/api/state`, { timeout: 15000 });
    } catch (error) {
      logger.warn(`[sentinel-bridge] state no disponible: ${error.message}`);
      return;
    }

    const anomalies = Array.isArray(state.anomalies) ? state.anomalies : [];
    const seen = { ...(store.get('sentinelAlerts', {}) || {}) };
    const now = Date.now();
    const current = new Set();
    let changed = false;

    for (const anomaly of anomalies) {
      const key = this.fingerprint(anomaly);
      current.add(key);
      if (seen[key]) {
        seen[key].last = now;
        continue;
      }

      seen[key] = { first: now, last: now };
      changed = true;

      const critical = String(anomaly.severity || '').toLowerCase() === 'critical';
      const actions = Array.isArray(anomaly.telegram_actions)
        ? anomaly.telegram_actions
        : [];
      const keyboard = actions
        .filter((action) => action && action.label && action.callback_data)
        .map((action) => [{ text: action.label, callback_data: action.callback_data }]);

      if (keyboard.length === 0) {
        keyboard.push([
          {
            text: '🩺 Diagnosticar con IA',
            callback_data: `ai:diagnose:${slug(anomaly.component)}`,
          },
        ]);
      }
      keyboard.push([{ text: '⬅️ Menú IA', callback_data: 'menu:back' }]);

      const lines = [
        `${critical ? '🚨' : '⚠️'} <b>${critical ? 'CRÍTICO' : 'ADVERTENCIA'} · SENTINEL AI</b>`,
        `<b>${esc(anomaly.component || 'sistema')}</b>`,
        esc(anomaly.summary || 'Anomalía detectada'),
      ];
      if (anomaly.details) {
        const detail = typeof anomaly.details === 'string'
          ? anomaly.details
          : JSON.stringify(anomaly.details);
        lines.push(`<code>${esc(String(detail).slice(0, 500))}</code>`);
      }
      lines.push('', '<i>Acciones rápidas disponibles abajo.</i>');

      try {
        await this.notify(lines.join('\n'), {
          reply_markup: { inline_keyboard: keyboard },
        });
      } catch (error) {
        logger.error(`[sentinel-bridge] envío de alerta: ${error.message}`);
      }
    }

    for (const key of Object.keys(seen)) {
      if (!current.has(key)) {
        delete seen[key];
        changed = true;
      }
    }
    if (changed) store.set('sentinelAlerts', seen);
  }
}

module.exports = SentinelBridge;
