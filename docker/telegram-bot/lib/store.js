'use strict';

const fs = require('fs');
const path = require('path');
const config = require('../config');
const logger = require('./logger');

class Store {
  constructor() {
    this.file = path.join(config.dataDir, 'state.json');
    this.data = {};
    this.timer = null;
    this.load();
  }

  load() {
    try {
      const raw = fs.readFileSync(this.file, 'utf8');
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        this.data = parsed;
      }
    } catch (error) {
      if (error.code !== 'ENOENT') {
        logger.warn(`No se pudo leer ${this.file}: ${error.message}`);
      }
    }
  }

  get(key, fallback) {
    return Object.prototype.hasOwnProperty.call(this.data, key)
      ? this.data[key]
      : fallback;
  }

  set(key, value) {
    this.data[key] = value;
    this.scheduleSave();
    return value;
  }

  scheduleSave() {
    if (this.timer) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.save();
    }, 500);
    if (typeof this.timer.unref === 'function') this.timer.unref();
  }

  save() {
    try {
      fs.mkdirSync(config.dataDir, { recursive: true });
      const temporary = `${this.file}.tmp`;
      fs.writeFileSync(temporary, JSON.stringify(this.data, null, 2));
      fs.renameSync(temporary, this.file);
    } catch (error) {
      logger.error(`No se pudo guardar ${this.file}: ${error.message}`);
    }
  }

  flush() {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.save();
  }
}

module.exports = new Store();
