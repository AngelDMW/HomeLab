#!/bin/sh
# Supervisor mínimo: cerebro IA local + bot de Telegram en el mismo contenedor.
set -e

python3 -m ai_brain.server &
BRAIN_PID=$!
node --dns-result-order=ipv4first bot.js &
BOT_PID=$!

shutdown() {
  kill "$BRAIN_PID" "$BOT_PID" 2>/dev/null || true
  wait "$BRAIN_PID" 2>/dev/null || true
  wait "$BOT_PID" 2>/dev/null || true
  exit 0
}
trap shutdown TERM INT

while kill -0 "$BRAIN_PID" 2>/dev/null && kill -0 "$BOT_PID" 2>/dev/null; do
  sleep 2
done

echo "[start] uno de los procesos terminó; cerrando contenedor" >&2
shutdown
