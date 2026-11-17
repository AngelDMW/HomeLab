'use strict';

const http = require('http');
const config = require('../config');
const logger = require('../lib/logger');

function dockerRequest(method, pathname, options = {}) {
  const { body = null, timeout = 12000 } = options;
  return new Promise((resolve, reject) => {
    const payload = body === null ? null : Buffer.from(JSON.stringify(body));
    const headers = {};
    if (payload) {
      headers['Content-Type'] = 'application/json';
      headers['Content-Length'] = payload.length;
    }
    const req = http.request(
      {
        socketPath: config.docker.socketPath,
        path: pathname,
        method,
        headers,
        timeout,
      },
      (res) => {
        const chunks = [];
        res.on('data', (chunk) => chunks.push(chunk));
        res.on('end', () => {
          const buffer = Buffer.concat(chunks);
          let data = null;
          if (buffer.length > 0) {
            const text = buffer.toString('utf8');
            try {
              data = JSON.parse(text);
            } catch (error) {
              data = text;
            }
          }
          resolve({ status: res.statusCode || 0, headers: res.headers, data, buffer });
        });
      },
    );
    req.on('timeout', () => req.destroy(new Error('Docker socket timeout')));
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

function parseDockerLogs(buffer) {
  if (!Buffer.isBuffer(buffer) || buffer.length === 0) return '';
  const parts = [];
  let offset = 0;
  let frames = 0;

  while (offset + 8 <= buffer.length) {
    const stream = buffer[offset];
    const size = buffer.readUInt32BE(offset + 4);
    if (stream > 2 || offset + 8 + size > buffer.length) break;
    parts.push(buffer.slice(offset + 8, offset + 8 + size).toString('utf8'));
    offset += 8 + size;
    frames += 1;
  }

  const text =
    frames > 0 && offset === buffer.length ? parts.join('') : buffer.toString('utf8');
  return text
    .replace(/\u001b\[[0-9;]*[A-Za-z]/g, '')
    .replace(/\r/g, '');
}

function normalizeContainer(container) {
  const name = String((container.Names || [])[0] || '').replace(/^\//, '');
  const status = container.Status || '';
  let health = null;
  if (status.includes('(unhealthy)')) health = 'unhealthy';
  else if (status.includes('(healthy)')) health = 'healthy';
  else if (status.includes('(health: starting)')) health = 'starting';

  return {
    id: container.Id,
    name,
    image: container.Image,
    state: container.State,
    status,
    health,
    labels: container.Labels || {},
  };
}

async function listContainers() {
  try {
    const response = await dockerRequest('GET', '/containers/json?all=1');
    if (response.status >= 400) {
      throw new Error(`HTTP ${response.status}`);
    }
    const raw = Array.isArray(response.data)
      ? response.data
      : JSON.parse(response.buffer.toString('utf8'));
    return raw.map(normalizeContainer);
  } catch (error) {
    logger.error(`[docker] listContainers: ${error.message}`);
    return [];
  }
}

async function inspectContainer(name) {
  try {
    const response = await dockerRequest(
      'GET',
      `/containers/${encodeURIComponent(name)}/json`,
    );
    if (response.status >= 400) return null;
    return response.data;
  } catch (error) {
    logger.debug(`[docker] inspect ${name}: ${error.message}`);
    return null;
  }
}

async function restartContainer(name) {
  try {
    const response = await dockerRequest(
      'POST',
      `/containers/${encodeURIComponent(name)}/restart?t=10`,
      { timeout: 90000 },
    );
    return response.status === 204 || response.status === 200 || response.status === 304;
  } catch (error) {
    logger.error(`[docker] restart ${name}: ${error.message}`);
    return false;
  }
}

async function getLogs(name, tail = 15) {
  try {
    const response = await dockerRequest(
      'GET',
      `/containers/${encodeURIComponent(name)}/logs?stdout=1&stderr=1&tail=${tail}`,
    );
    const text = parseDockerLogs(response.buffer);
    return text
      .split('\n')
      .map((line) => line.trimEnd())
      .filter(Boolean)
      .slice(-tail)
      .join('\n')
      .slice(-4000);
  } catch (error) {
    logger.debug(`[docker] logs ${name}: ${error.message}`);
    return '';
  }
}

async function systemDf() {
  const response = await dockerRequest('GET', '/system/df', { timeout: 90000 });
  if (response.status >= 400) throw new Error(`Docker df HTTP ${response.status}`);
  return response.data;
}

async function pruneImages() {
  const response = await dockerRequest(
    'POST',
    '/images/prune?filters=%7B%22dangling%22%3A%5B%22true%22%5D%7D',
    { timeout: 60000 },
  );
  if (response.status >= 400) throw new Error(`Docker prune HTTP ${response.status}`);
  return response.data || { SpaceReclaimed: 0 };
}

async function pruneBuilder() {
  const response = await dockerRequest('POST', '/build/prune', { timeout: 60000 });
  if (response.status >= 400) throw new Error(`Docker prune HTTP ${response.status}`);
  return response.data || { SpaceReclaimed: 0 };
}

module.exports = {
  listContainers,
  inspectContainer,
  restartContainer,
  getLogs,
  systemDf,
  pruneImages,
  pruneBuilder,
  parseDockerLogs,
};
