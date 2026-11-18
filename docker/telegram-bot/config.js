'use strict';

require('dotenv').config();

const toInt = (value, fallback) => {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const env = process.env;
const timezone = env.BRIEFING_TZ || env.TZ || 'America/Santo_Domingo';

const expectedContainers = [
  { name: 'jellyfin', label: 'Jellyfin', port: 8096 },
  { name: 'jellyseerr', label: 'Seerr', port: 5055 },
  { name: 'radarr', label: 'Radarr', port: 7878 },
  { name: 'sonarr', label: 'Sonarr', port: 8989 },
  { name: 'prowlarr', label: 'Prowlarr', port: 9696 },
  { name: 'flaresolverr', label: 'FlareSolverr', port: 8191 },
  { name: 'qbittorrent', label: 'qBittorrent', port: 8081 },
  { name: 'nextcloud', label: 'Nextcloud', port: 8080 },
  { name: 'pihole', label: 'Pi-hole', port: 80 },
  { name: 'portainer', label: 'Portainer', port: 9000 },
  { name: 'optiplex-chatops', label: 'Telegram Bot', port: null },
];

module.exports = {
  version: '2.1.0',
  telegram: {
    token: env.TELEGRAM_TOKEN || '',
    chatId: toInt(env.CHAT_ID, 0),
  },
  ownerName: env.OWNER_NAME || 'Angel',
  selfName: 'optiplex-chatops',
  timezone,
  dataDir: env.DATA_DIR || '/app/data',
  hostRoot: env.HOST_ROOT || '/hostfs',
  startupMessage: String(env.STARTUP_MESSAGE || 'true').toLowerCase() !== 'false',
  briefing: {
    time: /^\d{2}:\d{2}$/.test(env.BRIEFING_TIME || '') ? env.BRIEFING_TIME : '08:00',
    catchUpMinutes: 180,
  },
  thresholds: {
    diskWarnGB: Number.parseFloat(env.DISK_WARN_GB || '35'),
    diskCriticalGB: Number.parseFloat(env.DISK_CRITICAL_GB || '20'),
    diskResumeGB: Number.parseFloat(env.DISK_RESUME_GB || '40'),
    stalledHours: Number.parseFloat(env.STALLED_HOURS || '6'),
    restartMax: 2,
    restartWaitMs: 20000,
  },
  docker: {
    socketPath: env.DOCKER_SOCKET || '/var/run/docker.sock',
  },
  node2: {
    name: env.NODE2_NAME || 'Nodo 2',
    host: env.NODE2_HOST || '192.168.1.4',
    tailscaleHost: env.NODE2_TAILSCALE_HOST || '',
    mac: env.NODE2_MAC || '',
    user: env.NODE2_SSH_USER || 'mrdavid',
    port: toInt(env.NODE2_SSH_PORT, 22),
    keyPath: env.NODE2_SSH_KEY || '/app/ssh/id_ed25519',
    wolBroadcast: env.NODE2_WOL_BROADCAST || '192.168.1.255',
    storagePath: env.NODE2_STORAGE_PATH || '/mnt/storage',
  },
  radarr: {
    url: 'http://127.0.0.1:7878',
    apiKey: env.RADARR_API_KEY || '',
    label: 'Radarr',
    kind: 'movie',
    category: 'radarr',
  },
  sonarr: {
    url: 'http://127.0.0.1:8989',
    apiKey: env.SONARR_API_KEY || '',
    label: 'Sonarr',
    kind: 'series',
    category: 'sonarr',
  },
  prowlarr: {
    url: 'http://127.0.0.1:9696',
    apiKey: env.PROWLARR_API_KEY || '',
    label: 'Prowlarr',
  },
  seerr: {
    url: 'http://127.0.0.1:5055',
    apiKey: env.JELLYSEERR_API_KEY || '',
    publicUrl: env.SEERR_PUBLIC_URL || 'http://100.98.35.64:5055',
  },
  jellyfin: {
    url: 'http://127.0.0.1:8096',
    apiKey: env.JELLYFIN_API_KEY || '',
    publicUrl: env.JELLYFIN_PUBLIC_URL || 'http://100.98.35.64:8096',
  },
  qbittorrent: {
    url: 'http://127.0.0.1:8081',
    username: env.QBITTORRENT_USER || '',
    password: env.QBITTORRENT_PASS || '',
  },
  pihole: {
    url: 'http://127.0.0.1',
    password: env.PIHOLE_PASSWORD || '',
  },
  urls: {
    jellyfin: env.JELLYFIN_PUBLIC_URL || 'http://100.98.35.64:8096',
    seerr: env.SEERR_PUBLIC_URL || 'http://100.98.35.64:5055',
    portainer: env.PORTAINER_PUBLIC_URL || 'https://100.98.35.64:9443',
    nextcloud: env.NEXTCLOUD_PUBLIC_URL || 'http://100.98.35.64:8080',
  },
  paths: {
    media: '/home/mrdavid/HomeLab/media',
    movies: '/home/mrdavid/HomeLab/media/peliculas',
    series: '/home/mrdavid/HomeLab/media/series',
    downloads: '/home/mrdavid/HomeLab/media/descargas',
    nextcloudLog:
      '/var/lib/docker/volumes/nextcloud_nextcloud_data/_data/data/nextcloud.log',
  },
  ai: {
    groqKey: env.GROQ_API_KEY || '',
    openrouterKey: env.OPENROUTER_API_KEY || '',
  },
  expectedContainers,
};
