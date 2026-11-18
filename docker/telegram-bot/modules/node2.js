'use strict';

const { execFile } = require('child_process');
const dgram = require('dgram');
const net = require('net');

const config = require('../config');
const logger = require('../lib/logger');
const { sleep } = require('../lib/format');

const NODE = config.node2;

const STATUS_SCRIPT = `
export LC_ALL=C
echo "HOST=$(hostname 2>/dev/null || cat /etc/hostname)"
echo "OS=$(grep -m1 '^PRETTY_NAME=' /etc/os-release 2>/dev/null | cut -d= -f2- | tr -d '"')"
echo "KERNEL=$(uname -r)"
echo "UPTIME=$(cut -d. -f1 /proc/uptime)"
echo "LOAD=$(cut -d' ' -f1-3 /proc/loadavg)"
echo "CPUS=$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN)"
echo "MEM=$(free -b 2>/dev/null | awk '/^Mem:/{print $2" "$3" "$7}')"
echo "ROOT=$(df -B1 --output=size,used,avail / 2>/dev/null | tail -1 | awk '{print $1" "$2" "$3}')"
if mountpoint -q /mnt/storage 2>/dev/null; then
  echo "STORAGE=$(df -B1 --output=size,used,avail /mnt/storage 2>/dev/null | tail -1 | awk '{print $1" "$2" "$3}')"
else
  echo "STORAGE="
fi
echo "TEMP=$(for f in /sys/class/hwmon/hwmon*/temp1_input; do [ -r "$f" ] && cat "$f" && break; done 2>/dev/null)"
echo "DOCKER=$(docker ps -q 2>/dev/null | wc -l | tr -d ' ')/$(docker ps -aq 2>/dev/null | wc -l | tr -d ' ')"
echo "IPS=$(hostname -I 2>/dev/null | xargs)"
echo "GPU=$(lspci 2>/dev/null | awk -F': ' '/VGA compatible controller|Display controller/ {print $2; exit}')"
echo "CPU1=$(grep '^cpu ' /proc/stat)"
sleep 0.4
echo "CPU2=$(grep '^cpu ' /proc/stat)"
`.trim();

function hosts() {
  return [...new Set([NODE.host, NODE.tailscaleHost].filter(Boolean))];
}

function sshArgs(host) {
  return [
    '-o',
    'BatchMode=yes',
    '-o',
    'ConnectTimeout=5',
    '-o',
    'StrictHostKeyChecking=accept-new',
    '-o',
    `UserKnownHostsFile=${config.dataDir}/known_hosts`,
    '-o',
    'IdentitiesOnly=yes',
    '-o',
    'LogLevel=ERROR',
    '-p',
    String(NODE.port),
    '-i',
    NODE.keyPath,
    `${NODE.user}@${host}`,
  ];
}

function runSsh(host, script, timeout = 25000) {
  const encoded = Buffer.from(script).toString('base64');
  const remote = `echo ${encoded}|base64 -d|bash`;
  return new Promise((resolve, reject) => {
    execFile(
      'ssh',
      [...sshArgs(host), remote],
      { timeout, maxBuffer: 1024 * 1024 },
      (error, stdout, stderr) => {
        if (error) {
          const detail = String(stderr || stdout || error.message || '').trim();
          error.message = detail || error.message;
          return reject(error);
        }
        resolve(String(stdout || '').trim());
      },
    );
  });
}

function parseKeyValues(output) {
  const data = {};
  for (const line of String(output || '').split('\n')) {
    const index = line.indexOf('=');
    if (index > 0) data[line.slice(0, index)] = line.slice(index + 1).trim();
  }
  return data;
}

function parseCpu(line) {
  const values = String(line || '')
    .trim()
    .split(/\s+/)
    .slice(1)
    .map((value) => Number(value) || 0);
  const idle = (values[3] || 0) + (values[4] || 0);
  const total = values.reduce((sum, value) => sum + value, 0);
  return { idle, total };
}

function parseTriplet(value) {
  const [total, used, free] = String(value || '')
    .trim()
    .split(/\s+/)
    .map((entry) => Number(entry) || 0);
  if (!total) return null;
  return {
    total,
    used,
    free,
    percent: Math.max(0, Math.min(100, (used / total) * 100)),
  };
}

function probe(host, port = NODE.port, timeout = 1800) {
  return new Promise((resolve) => {
    const socket = net.connect({ host, port });
    let settled = false;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolve(ok);
    };
    socket.setTimeout(timeout);
    socket.once('connect', () => finish(true));
    socket.once('timeout', () => finish(false));
    socket.once('error', () => finish(false));
  });
}

function parseStatus(host, output) {
  const data = parseKeyValues(output);
  const memory = parseTriplet(data.MEM);
  const diskRoot = parseTriplet(data.ROOT);
  const diskStorage = parseTriplet(data.STORAGE);
  const first = parseCpu(data.CPU1);
  const second = parseCpu(data.CPU2);
  const cpuPercent =
    second.total > first.total
      ? Math.max(
          0,
          Math.min(100, (1 - (second.idle - first.idle) / (second.total - first.total)) * 100),
        )
      : null;
  const rawTemp = Number(data.TEMP);
  const temperature =
    Number.isFinite(rawTemp) && rawTemp > 1000 && rawTemp < 120000
      ? rawTemp / 1000
      : null;
  const [dockerRunning, dockerTotal] = String(data.DOCKER || '')
    .split('/')
    .map((entry) => Number(entry) || 0);

  return {
    online: true,
    ssh: true,
    host,
    name: NODE.name,
    mac: NODE.mac,
    hostname: data.HOST || 'homelab-node2',
    os: (data.OS || 'Debian GNU/Linux').replace(/^"|"$/g, ''),
    kernel: data.KERNEL || '',
    uptime: Number(data.UPTIME) || 0,
    load: String(data.LOAD || '')
      .split(/\s+/)
      .map((entry) => Number(entry) || 0),
    cpus: Number(data.CPUS) || 0,
    cpuPercent,
    memory,
    diskRoot,
    diskStorage,
    temperature,
    docker: { running: dockerRunning, total: dockerTotal },
    ips: String(data.IPS || '').split(/\s+/).filter(Boolean),
    gpu: data.GPU || '',
    checkedAt: Date.now(),
  };
}

async function status() {
  let lastError = null;
  for (const host of hosts()) {
    try {
      const output = await runSsh(host, STATUS_SCRIPT);
      return parseStatus(host, output);
    } catch (error) {
      lastError = error;
      logger.debug(`[node2] SSH ${host}: ${error.message}`);
    }
  }

  const reachable = [];
  for (const host of hosts()) {
    if (await probe(host)) reachable.push(host);
  }
  if (reachable.length > 0) {
    return {
      online: true,
      ssh: false,
      host: reachable[0],
      name: NODE.name,
      mac: NODE.mac,
      error: lastError?.message || 'SSH no disponible',
      checkedAt: Date.now(),
    };
  }

  return {
    online: false,
    ssh: false,
    host: NODE.host,
    name: NODE.name,
    mac: NODE.mac,
    tailscaleHost: NODE.tailscaleHost,
    error: lastError?.message || null,
    checkedAt: Date.now(),
  };
}

function magicPacket(mac) {
  const hex = String(mac || '').replace(/[^0-9a-f]/gi, '');
  if (hex.length !== 12) {
    throw new Error(`MAC inválida: ${mac}`);
  }
  const target = Buffer.from(hex, 'hex');
  const packet = Buffer.alloc(6 + 16 * 6, 0xff);
  for (let index = 0; index < 16; index += 1) {
    target.copy(packet, 6 + index * 6);
  }
  return packet;
}

function sendUdp(packet, address, port) {
  return new Promise((resolve, reject) => {
    const socket = dgram.createSocket('udp4');
    socket.once('error', (error) => {
      socket.close();
      reject(error);
    });
    socket.bind(() => {
      try {
        socket.setBroadcast(true);
      } catch (error) {
        logger.debug(`[node2] broadcast: ${error.message}`);
      }
      socket.send(packet, port, address, (error) => {
        socket.close();
        if (error) reject(error);
        else resolve(`${address}:${port}`);
      });
    });
  });
}

async function wake() {
  const packet = magicPacket(NODE.mac);
  const addresses = [...new Set([NODE.wolBroadcast, '255.255.255.255'].filter(Boolean))];
  const sent = [];
  for (const address of addresses) {
    for (const port of [9, 7]) {
      try {
        sent.push(await sendUdp(packet, address, port));
      } catch (error) {
        logger.warn(`[node2] WoL ${address}:${port}: ${error.message}`);
      }
    }
  }
  if (sent.length === 0) {
    throw new Error('no se pudo enviar el magic packet');
  }
  logger.info(`[node2] WoL enviado a ${NODE.mac} (${sent.join(', ')})`);
  return sent;
}

async function shutdown() {
  let lastError = null;
  for (const host of hosts()) {
    try {
      const output = await runSsh(
        host,
        "nohup sh -c 'sleep 1; sudo -n /usr/sbin/poweroff' >/dev/null 2>&1 & echo queued",
        8000,
      );
      if (!output.includes('queued')) {
        throw new Error(output || 'respuesta inesperada');
      }
      await sleep(2000);
      logger.info(`[node2] apagado solicitado vía ${host}`);
      return { ok: true, host };
    } catch (error) {
      lastError = error;
      logger.warn(`[node2] apagado ${host}: ${error.message}`);
    }
  }
  return { ok: false, error: lastError?.message || 'sin acceso SSH' };
}

module.exports = { status, wake, shutdown, probe };
