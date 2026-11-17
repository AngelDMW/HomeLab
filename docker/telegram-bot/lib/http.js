'use strict';

const http = require('http');
const https = require('https');
const net = require('net');

function rawRequest(urlString, options = {}) {
  const {
    method = 'GET',
    headers = {},
    body = null,
    timeout = 10000,
    family = 4,
    rejectUnauthorized = true,
    maxBytes = 2 * 1024 * 1024,
  } = options;

  return new Promise((resolve, reject) => {
    let url;
    try {
      url = new URL(urlString);
    } catch (error) {
      reject(new Error(`URL inválida: ${urlString}`));
      return;
    }

    const isHttps = url.protocol === 'https:';
    const finalHeaders = { 'User-Agent': 'OptiplexBrain/2.0', ...headers };
    let payload = null;

    if (body !== null && body !== undefined) {
      if (Buffer.isBuffer(body)) payload = body;
      else if (typeof body === 'string') payload = Buffer.from(body);
      else payload = Buffer.from(JSON.stringify(body));
      const hasContentType = Object.keys(finalHeaders).some(
        (name) => name.toLowerCase() === 'content-type',
      );
      if (!hasContentType) finalHeaders['Content-Type'] = 'application/json';
      finalHeaders['Content-Length'] = payload.length;
    }

    const requestOptions = {
      method,
      hostname: url.hostname,
      port: url.port || (isHttps ? 443 : 80),
      path: `${url.pathname}${url.search}`,
      headers: finalHeaders,
      family,
      timeout,
    };
    if (isHttps) requestOptions.rejectUnauthorized = rejectUnauthorized;

    const req = (isHttps ? https : http).request(requestOptions, (res) => {
      const chunks = [];
      let size = 0;
      res.on('data', (chunk) => {
        size += chunk.length;
        if (size <= maxBytes) chunks.push(chunk);
      });
      res.on('end', () => {
        const buffer = Buffer.concat(chunks);
        const text = buffer.toString('utf8');
        const contentType = String(res.headers['content-type'] || '');
        let data = text;
        if (contentType.includes('json') || /^\s*[[{]/.test(text)) {
          try {
            data = JSON.parse(text);
          } catch (error) {
            data = text;
          }
        }
        resolve({
          status: res.statusCode || 0,
          headers: res.headers,
          data,
          text,
          buffer,
          truncated: size > maxBytes,
        });
      });
      res.on('error', reject);
    });

    req.on('timeout', () => {
      req.destroy(new Error(`Timeout (${timeout} ms): ${urlString}`));
    });
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

async function getJson(url, options = {}) {
  const response = await rawRequest(url, options);
  if (response.status < 200 || response.status >= 300) {
    throw new Error(`${url} → HTTP ${response.status}`);
  }
  return response.data;
}

async function probe(url, options = {}) {
  const { timeout = 6000, headers = {}, acceptedStatus = null } = options;
  const started = Date.now();
  try {
    const response = await rawRequest(url, {
      timeout,
      headers,
      rejectUnauthorized: false,
      maxBytes: 4096,
    });
    const ok = Array.isArray(acceptedStatus)
      ? acceptedStatus.includes(response.status)
      : response.status < 500;
    return {
      ok,
      status: response.status,
      ms: Date.now() - started,
    };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      ms: Date.now() - started,
      error: error.message,
    };
  }
}

function tcpProbe(host, port, timeout = 3000) {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const socket = net.connect({ host, port, family: 4 });
    let settled = false;

    const finish = (error) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      if (error) reject(error);
      else resolve(Date.now() - started);
    };

    socket.setTimeout(timeout);
    socket.once('connect', () => finish());
    socket.once('timeout', () => finish(new Error('TCP timeout')));
    socket.once('error', (error) => finish(error));
  });
}

module.exports = { rawRequest, getJson, probe, tcpProbe };
