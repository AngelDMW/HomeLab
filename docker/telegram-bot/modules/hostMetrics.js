'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const config = require('../config');
const logger = require('../lib/logger');
const { probe, tcpProbe } = require('../lib/http');
const { sleep } = require('../lib/format');

const HOST = config.hostRoot;
let cpuCache = { at: 0, value: 0 };
let collectCache = { at: 0, data: null };

async function readFileSafe(target) {
  try {
    return await fs.promises.readFile(target, 'utf8');
  } catch (error) {
    return null;
  }
}

async function readCpuSample() {
  const stat = await readFileSafe(path.join(HOST, 'proc/stat'));
  if (!stat) return null;
  const line = stat.split('\n').find((entry) => entry.startsWith('cpu '));
  if (!line) return null;
  const numbers = line
    .trim()
    .split(/\s+/)
    .slice(1)
    .map((value) => Number(value) || 0);
  const idle = (numbers[3] || 0) + (numbers[4] || 0);
  const total = numbers.reduce((sum, value) => sum + value, 0);
  return { total, idle };
}

async function cpuUsage() {
  if (Date.now() - cpuCache.at < 4000) return cpuCache.value;
  const first = await readCpuSample();
  await sleep(250);
  const second = await readCpuSample();
  if (!first || !second || second.total <= first.total) return cpuCache.value;
  const total = second.total - first.total;
  const idle = second.idle - first.idle;
  const value = Math.max(0, Math.min(100, (1 - idle / total) * 100));
  cpuCache = { at: Date.now(), value };
  return value;
}

async function memoryInfo() {
  const meminfo = await readFileSafe(path.join(HOST, 'proc/meminfo'));
  if (!meminfo) return null;
  const read = (key) => {
    const match = meminfo.match(new RegExp(`^${key}:\\s+(\\d+) kB`, 'm'));
    return match ? Number(match[1]) * 1024 : 0;
  };
  const total = read('MemTotal');
  const available = read('MemAvailable') || read('MemFree');
  const used = Math.max(0, total - available);
  const swapTotal = read('SwapTotal');
  const swapFree = read('SwapFree');
  return {
    total,
    available,
    used,
    percent: total > 0 ? (used / total) * 100 : 0,
    swapTotal,
    swapUsed: Math.max(0, swapTotal - swapFree),
  };
}

async function cpuCount() {
  try {
    const cpuinfo = fs.readFileSync(path.join(HOST, 'proc/cpuinfo'), 'utf8');
    const count = (cpuinfo.match(/^processor\s*:/gm) || []).length;
    if (count > 0) return count;
  } catch (error) {
    // fall through to Node's view
  }
  return os.cpus().length;
}

async function loadInfo() {
  const loadavg = await readFileSafe(path.join(HOST, 'proc/loadavg'));
  if (!loadavg) return null;
  const parts = loadavg.trim().split(/\s+/).map(Number);
  return { one: parts[0] || 0, five: parts[1] || 0, fifteen: parts[2] || 0 };
}

async function uptimeSeconds() {
  const uptime = await readFileSafe(path.join(HOST, 'proc/uptime'));
  if (!uptime) return 0;
  return Number.parseFloat(uptime.split(' ')[0]) || 0;
}

async function diskInfo() {
  const stats = await fs.promises.statfs(HOST);
  const total = stats.blocks * stats.bsize;
  const free = stats.bavail * stats.bsize;
  const used = Math.max(0, total - free);
  return {
    total,
    free,
    used,
    percent: total > 0 ? (used / total) * 100 : 0,
    freeGB: free / 1024 ** 3,
    totalGB: total / 1024 ** 3,
  };
}

async function temperatureInfo() {
  const candidates = [];
  const hwmonBase = path.join(HOST, 'sys/class/hwmon');
  let dirs = [];
  try {
    dirs = await fs.promises.readdir(hwmonBase);
  } catch (error) {
    dirs = [];
  }

  for (const dir of dirs) {
    const full = path.join(hwmonBase, dir);
    let files = [];
    try {
      files = await fs.promises.readdir(full);
    } catch (error) {
      continue;
    }
    for (const file of files.filter((entry) => /^temp\d+_input$/.test(entry))) {
      const raw = await readFileSafe(path.join(full, file));
      const value = Number.parseFloat(raw || '');
      if (!Number.isFinite(value) || value < 1000 || value > 120000) continue;
      const labelPath = path.join(full, file.replace('_input', '_label'));
      const label = (await readFileSafe(labelPath)) || '';
      candidates.push({ celsius: value / 1000, label: label.trim() });
    }
  }

  const thermalBase = path.join(HOST, 'sys/class/thermal');
  let zones = [];
  try {
    zones = await fs.promises.readdir(thermalBase);
  } catch (error) {
    zones = [];
  }
  for (const zone of zones.filter((entry) => /^thermal_zone\d+$/.test(entry))) {
    const raw = await readFileSafe(path.join(thermalBase, zone, 'temp'));
    const value = Number.parseFloat(raw || '');
    if (!Number.isFinite(value) || value < 1000 || value > 120000) continue;
    const label = (await readFileSafe(path.join(thermalBase, zone, 'type'))) || '';
    candidates.push({ celsius: value / 1000, label: label.trim() });
  }

  const preferred = candidates.filter((entry) =>
    /package|core|tdie|tctl/i.test(entry.label),
  );
  const pool = (preferred.length > 0 ? preferred : candidates).filter(
    (entry) => entry.celsius > 0 && entry.celsius < 110,
  );
  if (pool.length === 0) return null;
  const hottest = pool.reduce((best, entry) =>
    entry.celsius > best.celsius ? entry : best,
  );
  return { celsius: hottest.celsius, source: hottest.label || 'sensor' };
}

async function hostname() {
  const value = await readFileSafe(path.join(HOST, 'etc/hostname'));
  return (value || os.hostname()).trim();
}

async function networkStatus() {
  const [internet, tailscale] = await Promise.all([
    probe('https://www.google.com/generate_204', { timeout: 6000 }),
    tcpProbe('100.100.100.100', 53, 3500)
      .then((ms) => ({ ok: true, ms }))
      .catch((error) => ({ ok: false, error: error.message })),
  ]);
  return { internet, tailscale };
}

async function collect(options = {}) {
  const { force = false, includeNetwork = true } = options;
  if (!force && collectCache.data && Date.now() - collectCache.at < 5000) {
    return collectCache.data;
  }

  const [cpu, memory, temperature, disk, uptime, load, cores, host] =
    await Promise.all([
      cpuUsage(),
      memoryInfo(),
      temperatureInfo(),
      diskInfo(),
      uptimeSeconds(),
      loadInfo(),
      cpuCount(),
      hostname(),
    ]);

  const network = includeNetwork
    ? await networkStatus()
    : collectCache.data?.network || { internet: null, tailscale: null };

  const data = {
    at: Date.now(),
    hostname: host,
    cpu: { percent: cpu, count: cores },
    memory,
    temperature,
    disk,
    uptime,
    load,
    network,
  };
  collectCache = { at: Date.now(), data };
  logger.debug(`[host] muestra: CPU ${cpu.toFixed(1)}%`);
  return data;
}

module.exports = { collect, networkStatus, diskInfo, temperatureInfo };
