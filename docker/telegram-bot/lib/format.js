'use strict';

function formatBytes(bytes, decimals = 1) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value < 0) return '—';
  if (value < 1024) return `${Math.round(value)} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let current = value / 1024;
  let index = 0;
  while (current >= 1024 && index < units.length - 1) {
    current /= 1024;
    index += 1;
  }
  return `${current.toFixed(decimals)} ${units[index]}`;
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function progressBar(percent, size = 8) {
  const value = Math.max(0, Math.min(100, Number(percent) || 0));
  const filled = Math.round((value / 100) * size);
  return '▰'.repeat(filled) + '▱'.repeat(Math.max(0, size - filled));
}

function escapeHtml(text) {
  return String(text == null ? '' : text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function cleanTitle(name) {
  return String(name || '')
    .replace(/^\[[^\]]+\]\s*/, '')
    .replace(/\.(mkv|mp4|avi|mov|srt|nfo|torrent)$/i, '')
    .replace(/[._]+/g, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

function lookupTitle(name) {
  let title = cleanTitle(name);
  const season = title.match(/\bS\d{1,2}E\d{1,2}\b/i);
  if (season) title = title.slice(0, season.index).trim();
  const quality = title.search(
    /\b(2160p|1080p|720p|480p|WEB[- ]?DL|WEBRip|BluRay|BRRip|HDTV|x264|x265|HEVC|AAC|DTS|REPACK|PROPER|MULTi|DUAL|LATINO)\b/i,
  );
  if (quality > 0) title = title.slice(0, quality).trim();
  return title;
}

function detectLanguage(name) {
  const text = String(name || '');
  if (/latino/i.test(text)) return 'Latino';
  if (/castellano|espa[nñ]ol|spanish/i.test(text)) return 'Español';
  if (/dual/i.test(text)) return 'Dual';
  if (/vose|subtitulad/i.test(text)) return 'VOSE';
  return 'Inglés / original';
}

function clampText(text, limit = 4000) {
  const value = String(text || '');
  return value.length > limit ? `${value.slice(0, limit - 1)}…` : value;
}

function nowInTimezone(timezone) {
  return new Date().toLocaleString('sv-SE', { timeZone: timezone, hour12: false });
}

function dateKeyInTimezone(timezone) {
  return new Date().toLocaleDateString('en-CA', { timeZone: timezone });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

module.exports = {
  formatBytes,
  formatDuration,
  progressBar,
  escapeHtml,
  cleanTitle,
  lookupTitle,
  detectLanguage,
  clampText,
  nowInTimezone,
  dateKeyInTimezone,
  sleep,
};
