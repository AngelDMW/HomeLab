#!/usr/bin/env bash
# HomeLab Corp — respaldo 3-2-1 de configuraciones críticas (sólo Nodo 1 → offload Nodo 2).
# NUNCA copia /media, datos de Nextcloud ni bibliotecas de Immich.
set -Eeuo pipefail

DEST_ROOT=/mnt/node2-storage/backups-criticos
SENTINEL_DB=/home/mrdavid/HomeLab/docker/sentinel/data/sentinel.db
NEXUS_DB=/home/mrdavid/HomeLab/docker/nexus/data/nexus.sqlite3
TS="$(date +%Y%m%d_%H%M%S)"
DST="$DEST_ROOT/$TS"
mkdir -p "$DST"

fail() { printf '{"ok":false,"error":%s}\n' "$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$1")"; exit 1; }
size_of() { [ -f "$1" ] && stat -c %s "$1" || echo 0; }

# 1) PostgreSQL de Immich (dump lógico consistente, sin tocar la biblioteca)
PGUSER="$(docker exec immich-postgres printenv POSTGRES_USER)" || fail 'no se pudo leer POSTGRES_USER'
PGDB="$(docker exec immich-postgres printenv POSTGRES_DB)" || fail 'no se pudo leer POSTGRES_DB'
docker exec immich-postgres pg_dump -Fc -U "$PGUSER" "$PGDB" > "$DST/immich_postgres.dump" \
  || fail 'pg_dump de Immich falló'
docker exec -i immich-postgres pg_restore -l < "$DST/immich_postgres.dump" > /dev/null \
  || fail 'pg_restore -l no pudo validar el dump de Immich'

# 2) Configuración de Nextcloud (config.php; NO el datadir)
docker cp nextcloud:/var/www/html/config/config.php "$DST/nextcloud_config.php" >/dev/null 2>&1 \
  || fail 'docker cp de nextcloud config.php falló'
grep -q '<?php' "$DST/nextcloud_config.php" || fail 'config.php de Nextcloud no válido'

# 3) SQLite de Sentinel y Nexus mediante API oficial de backup (consistente con WAL)
python3 - "$SENTINEL_DB" "$DST/sentinel.sqlite3" "$NEXUS_DB" "$DST/nexus.sqlite3" <<'PY' || fail 'backup SQLite consistente falló'
import sqlite3, sys
for src, dst in ((sys.argv[1], sys.argv[2]), (sys.argv[3], sys.argv[4])):
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=15) as origin:
        with sqlite3.connect(dst, timeout=15) as target:
            origin.backup(target)
PY

# 4) Verificación de integridad de los SQLite generados
for db in sentinel.sqlite3 nexus.sqlite3; do
  result="$(python3 -c 'import sqlite3,sys;print(sqlite3.connect(sys.argv[1]).execute("PRAGMA integrity_check").fetchone()[0])' "$DST/$db")" \
    || fail "integrity_check falló para $db"
  [ "$result" = "ok" ] || fail "integrity_check de $db devolvió $result"
done

# 5) Manifiesto con tamaños y verificación de integridad
python3 - "$DST" "$TS" <<'PY' || fail 'no se pudo escribir el manifiesto'
import json, os, sys
dst, ts = sys.argv[1], sys.argv[2]
files = {}
for name in sorted(os.listdir(dst)):
    path = os.path.join(dst, name)
    if os.path.isfile(path):
        files[name] = {"bytes": os.path.getsize(path), "nonempty": os.path.getsize(path) > 0}
required = ("immich_postgres.dump", "nextcloud_config.php", "sentinel.sqlite3", "nexus.sqlite3")
missing = [name for name in required if not files.get(name, {}).get("nonempty")]
manifest = {
    "ok": not missing,
    "ts": ts,
    "kind": "corp-criticos-3-2-1",
    "source": "nodo1",
    "destination": "nodo2:/mnt/storage/homelab-offload/backups-criticos",
    "files": files,
    "missing_or_empty": missing,
    "note": "n8n workflows y ledger se añaden desde el Nodo 2; /media, Nextcloud data e Immich library NO se incluyen",
}
with open(os.path.join(dst, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
print(json.dumps(manifest, ensure_ascii=False))
PY

# 6) Retención: 7 días, exclusivamente dentro de backups-criticos
find "$DEST_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +7 -exec rm -rf -- {} +

# El servicio del Nodo 2 (uid 1000) debe poder completar el respaldo.
chown -R 1000:1000 "$DST"
