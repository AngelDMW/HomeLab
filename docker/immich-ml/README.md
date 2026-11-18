# Immich Machine Learning (Nodo 2)

Despliegue en el Nodo 2 (`192.168.1.4`, alias `homelab-node2`). El `immich-server` del Nodo 1 apunta a `http://192.168.1.4:3003`.

```bash
ssh homelab-node2 "mkdir -p /home/mrdavid/HomeLab/docker/immich-ml /mnt/storage/immich-ml-cache"
# copiar docker-compose.yml y .env al Nodo 2 y luego:
ssh homelab-node2 "cd /home/mrdavid/HomeLab/docker/immich-ml && docker compose up -d"
# detener cuando no se necesite (bajo demanda):
# ssh homelab-node2 "cd /home/mrdavid/HomeLab/docker/immich-ml && docker compose stop"
```

- Puerto: `3003`
- Límite: `mem_limit: 2560m`, `cpus: 6.0`
- Caché de modelos: `/mnt/storage/immich-ml-cache` (HDD Toshiba, **no** SSD del sistema)
