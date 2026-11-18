#!/usr/bin/env bash
set -euo pipefail

NODE2_HOST="192.168.1.4"
NODE2_USER="mrdavid"
NODE2_BACKUP_DIR="/mnt/storage/backups/immich"
LOCAL_LIBRARY="/home/mrdavid/HomeLab/docker/immich/library"
LOCAL_DUMP_DIR="/home/mrdavid/HomeLab/docker/immich/backups"
DB_CONTAINER="immich-postgres"
DB_USER="postgres"
DB_NAME="immich"
LOG_TAG="backup_immich_to_node2"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new)

log() { logger -t "$LOG_TAG" -- "$*" 2>/dev/null || true; echo "[$(date -Is)] $*"; }

mkdir -p "$LOCAL_DUMP_DIR"

# Salir limpiamente si el Nodo 2 no responde por SSH
if ! ssh "${SSH_OPTS[@]}" "${NODE2_USER}@${NODE2_HOST}" "echo ok" >/dev/null 2>&1; then
  log "Nodo 2 (${NODE2_HOST}) no disponible por SSH; se omite respaldo sin error."
  exit 0
fi

TS="$(date +%Y%m%d_%H%M%S)"
DUMP_FILE="${LOCAL_DUMP_DIR}/immich_db_${TS}.sql.gz"

log "Nodo 2 disponible: iniciando dump de base de datos Immich."
docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" | gzip > "$DUMP_FILE"

# Mantener dumps locales recientes (7 días)
find "$LOCAL_DUMP_DIR" -type f -name "immich_db_*.sql.gz" -mtime +7 -delete 2>/dev/null || true

log "Sincronizando library y dumps hacia Nodo 2:${NODE2_BACKUP_DIR}."
ssh "${SSH_OPTS[@]}" "${NODE2_USER}@${NODE2_HOST}" "mkdir -p ${NODE2_BACKUP_DIR}/library ${NODE2_BACKUP_DIR}/db"
rsync -a --delete "${LOCAL_LIBRARY}/" "${NODE2_USER}@${NODE2_HOST}:${NODE2_BACKUP_DIR}/library/"
rsync -a "${LOCAL_DUMP_DIR}/" "${NODE2_USER}@${NODE2_HOST}:${NODE2_BACKUP_DIR}/db/"

log "Respaldo Immich hacia Nodo 2 completado correctamente."
