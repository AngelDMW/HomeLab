'use strict';

const config = require('../config');

const LEVELS = { debug: 0, info: 1, warn: 2, error: 3 };
const threshold = LEVELS[(process.env.LOG_LEVEL || 'info').toLowerCase()] ?? 1;

function stamp() {
  return new Date().toLocaleString('sv-SE', {
    timeZone: config.timezone,
    hour12: false,
  });
}

function write(level, args) {
  if (LEVELS[level] < threshold) return;
  const consoleFn = level === 'debug' ? 'log' : level;
  console[consoleFn](`[${stamp()}] [${level.toUpperCase()}]`, ...args);
}

module.exports = {
  debug: (...args) => write('debug', args),
  info: (...args) => write('info', args),
  warn: (...args) => write('warn', args),
  error: (...args) => write('error', args),
};
