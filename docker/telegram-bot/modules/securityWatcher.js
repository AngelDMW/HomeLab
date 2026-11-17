'use strict';

const fs = require('fs');
const config = require('../config');
const logger = require('../lib/logger');
const store = require('../lib/store');
const { escapeHtml: esc } = require('../lib/format');

const WATCHED_PORTS = {
  8080: 'Nextcloud',
  8000: 'Portainer',
  9000: 'Portainer',
  9443: 'Portainer',
};

function isPrivateIPv4(ip) {
  if (!ip) return true;
  if (ip.startsWith('10.') || ip.startsWith('127.') || ip.startsWith('192.168.')) {
    return true;
  }
  if (ip.startsWith('169.254.')) return true;
  if (/^172\.(1[6-9]|2\d|3[01])\./.test(ip)) return true;
  if (/^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(ip)) return true;
  return false;
}

function hexToIPv4(hex) {
  if (!/^[0-9A-Fa-f]{8}$/.test(hex)) return null;
  const value = Number.parseInt(hex, 16);
  return [value & 255, (value >> 8) & 255, (value >> 16) & 255, (value >> 24) & 255].join(
    '.',
  );
}

function readEstablishedConnections() {
  const connections = [];
  for (const file of ['/proc/net/tcp', '/proc/net/tcp6']) {
    let content;
    try {
      content = fs.readFileSync(`${config.hostRoot}${file}`, 'utf8');
    } catch (error) {
      continue;
    }
    for (const line of content.split('\n').slice(1)) {
      const fields = line.trim().split(/\s+/);
      if (fields.length < 4 || fields[3] !== '01') continue;
      const [localAddress, localPort] = fields[1].split(':');
      const [remoteAddress, remotePort] = fields[2].split(':');
      const port = Number.parseInt(localPort, 16);
      if (!WATCHED_PORTS[port]) continue;
      let remoteIp = null;
      if (file.endsWith('tcp')) remoteIp = hexToIPv4(remoteAddress);
      else if (/^0{24}FFFF/i.test(remoteAddress)) {
        remoteIp = hexToIPv4(remoteAddress.slice(-8));
      } else {
        continue;
      }
      if (!remoteIp || isPrivateIPv4(remoteIp)) continue;
      connections.push({
        service: WATCHED_PORTS[port],
        localPort: port,
        remoteIp,
        remotePort: Number.parseInt(remotePort, 16),
        key: `${remoteIp}:${port}`,
      });
    }
  }
  return connections;
}

function parseNextcloudLine(line) {
  try {
    const entry = JSON.parse(line);
    return {
      remoteAddr: entry.remoteAddr || entry.data?.remoteAddr || '',
      user: entry.user || entry.data?.user || '',
      message: String(entry.message || ''),
      app: entry.app || entry.data?.app || '',
    };
  } catch (error) {
    const remote = line.match(/"remoteAddr":"([^"]+)"/);
    const message = line.match(/"message":"([^"]+)"/);
    if (!remote) return null;
    return {
      remoteAddr: remote[1],
      user: '',
      message: message ? message[1] : '',
      app: '',
    };
  }
}

class SecurityWatcher {
  constructor({ notify }) {
    this.notify = notify;
  }

  async checkConnections() {
    const seen = store.get('securityConnections', {});
    const now = Date.now();
    for (const connection of readEstablishedConnections()) {
      if (seen[connection.key] && now - seen[connection.key] < 6 * 3600 * 1000) {
        continue;
      }
      seen[connection.key] = now;
      await this.notify(
        [
          '🔐 <b>Posible acceso externo</b>',
          `Servicio: <b>${esc(connection.service)}</b> (puerto ${connection.localPort})`,
          `IP remota: <code>${esc(connection.remoteIp)}</code>`,
          'La conexión proviene de una red no privada. Verifica que seas tú.',
        ].join('\n'),
      );
    }
    for (const key of Object.keys(seen)) {
      if (now - seen[key] > 24 * 3600 * 1000) delete seen[key];
    }
    store.set('securityConnections', seen);
  }

  async checkNextcloudLog() {
    const file = `${config.hostRoot}${config.paths.nextcloudLog}`;
    let stats;
    try {
      stats = await fs.promises.stat(file);
    } catch (error) {
      logger.debug(`[security] Nextcloud log inaccesible: ${error.message}`);
      return;
    }

    let offset = store.get('nextcloudLogOffset', null);
    if (offset === null || stats.size < offset) {
      store.set('nextcloudLogOffset', stats.size);
      return;
    }
    if (stats.size === offset) return;

    const length = Math.min(stats.size - offset, 256 * 1024);
    let buffer;
    try {
      const handle = await fs.promises.open(file, 'r');
      buffer = Buffer.alloc(length);
      await handle.read(buffer, 0, length, offset);
      await handle.close();
    } catch (error) {
      logger.debug(`[security] lectura Nextcloud log: ${error.message}`);
      return;
    }

    const text = buffer.toString('utf8');
    const lastNewline = text.lastIndexOf('\n');
    const complete = lastNewline >= 0 ? text.slice(0, lastNewline + 1) : '';
    if (!complete) return;
    store.set('nextcloudLogOffset', offset + Buffer.byteLength(complete, 'utf8'));

    const seen = store.get('securityLogins', {});
    const now = Date.now();
    for (const line of complete.split('\n')) {
      if (!line.includes('login') && !line.includes('Login')) continue;
      const parsed = parseNextcloudLine(line);
      if (!parsed || !parsed.remoteAddr) continue;
      if (isPrivateIPv4(parsed.remoteAddr)) continue;
      if (!/login/i.test(parsed.message)) continue;
      const key = `${parsed.remoteAddr}:${parsed.user}`;
      if (seen[key] && now - seen[key] < 6 * 3600 * 1000) continue;
      seen[key] = now;
      await this.notify(
        [
          '🔐 <b>Inicio de sesión externo en Nextcloud</b>',
          `Usuario: <b>${esc(parsed.user || 'desconocido')}</b>`,
          `IP remota: <code>${esc(parsed.remoteAddr)}</code>`,
        ].join('\n'),
      );
    }
    store.set('securityLogins', seen);
  }

  async check() {
    await this.checkConnections().catch((error) =>
      logger.debug(`[security] conexiones: ${error.message}`),
    );
    await this.checkNextcloudLog().catch((error) =>
      logger.debug(`[security] nextcloud: ${error.message}`),
    );
  }
}

module.exports = { SecurityWatcher, isPrivateIPv4, readEstablishedConnections };
