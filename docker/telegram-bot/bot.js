require('dotenv').config();
const TelegramBot = require('node-telegram-bot-api');
const { exec } = require('child_process');
const https = require('https');

const bot = new TelegramBot(process.env.TELEGRAM_TOKEN, { polling: true });
const myChatId = parseInt(process.env.CHAT_ID);
const apiKey = process.env.HF_API_KEY;

let conversacion = [{ role: "system", content: "Eres una IA en un Debian 12 de Angel David. Tienes control total. Para ejecutar comandos, usa exactamente <EXEC>comando</EXEC>." }];

function llamarHuggingFace(mensajes) {
    return new Promise((resolve, reject) => {
        const data = JSON.stringify({ model: "meta-llama/Meta-Llama-3-8B-Instruct", messages: mensajes, max_tokens: 800 });
        const options = {
            hostname: 'api-inference.huggingface.co',
            path: '/v1/chat/completions',
            method: 'POST',
            family: 4, // 🔴 LA SOLUCIÓN DEFINITIVA: Fuerza solo IPv4
            headers: { 'Authorization': `Bearer ${apiKey}`, 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) }
        };
        const req = https.request(options, res => {
            let body = '';
            res.on('data', c => body += c);
            res.on('end', () => res.statusCode === 200 ? resolve(JSON.parse(body).choices[0].message.content) : reject(new Error(`HTTP ${res.statusCode}: ${body.substring(0, 50)}`)));
        });
        req.on('error', reject);
        req.write(data); req.end();
    });
}

bot.on('message', async (msg) => {
    if (msg.chat.id !== myChatId) return;
    try {
        bot.sendChatAction(myChatId, 'typing');
        conversacion.push({ role: "user", content: msg.text });
        
        let respuesta = await llamarHuggingFace(conversacion);
        const match = respuesta.match(/<EXEC>(.*?)<\/EXEC>/);
        
        if (match) {
            bot.sendMessage(myChatId, `[Ejecutando: ${match[1]}]`);
            exec(match[1], async (err, stdout, stderr) => {
                conversacion.push({ role: "assistant", content: respuesta });
                conversacion.push({ role: "user", content: `Salida:\n${(stdout||stderr||err.message).substring(0,800)}` });
                let resp2 = await llamarHuggingFace(conversacion);
                conversacion.push({ role: "assistant", content: resp2 });
                bot.sendMessage(myChatId, resp2);
            });
        } else {
            conversacion.push({ role: "assistant", content: respuesta });
            bot.sendMessage(myChatId, respuesta);
        }
    } catch (e) {
        bot.sendMessage(myChatId, `⚠️ Fallo: ${e.message}`);
        conversacion.pop();
    }
});
