'use strict';

const config = require('../config');
const logger = require('../lib/logger');

let client = null;

function getClient() {
  if (!config.ai.groqKey) return null;
  if (!client) {
    const OpenAI = require('openai');
    client = new OpenAI({
      apiKey: config.ai.groqKey,
      baseURL: 'https://api.groq.com/openai/v1',
    });
  }
  return client;
}

const SYSTEM_PROMPT = [
  'Eres el copiloto del HomeLab de Angel (Dell OptiPlex 5040, Debian 12, Docker).',
  'Respondes en español, de forma breve y útil.',
  'No puedes ejecutar comandos ni acceder a archivos por ti mismo.',
  'Si te piden una acción, indica el botón o comando de Telegram adecuado (/mission, /status, /containers, /queue, /disk, /briefing, /selftest).',
  'Nunca inventes estados: si no tienes un dato, dilo.',
].join(' ');

async function chat(text, context = '') {
  const api = getClient();
  if (!api) return null;
  try {
    const completion = await api.chat.completions.create({
      model: 'llama-3.3-70b-versatile',
      max_tokens: 350,
      temperature: 0.4,
      messages: [
        { role: 'system', content: SYSTEM_PROMPT },
        {
          role: 'user',
          content: context
            ? `${text}\n\nContexto actual del servidor:\n${context}`
            : text,
        },
      ],
    });
    return completion.choices?.[0]?.message?.content?.trim() || null;
  } catch (error) {
    logger.warn(`[ai] ${error.message}`);
    return null;
  }
}

module.exports = { chat };
