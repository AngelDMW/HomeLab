# Agente Autónomo del HomeLab

## Regla Principal de Ejecución
- Todo el proyecto y los servicios viven en el servidor remoto conectado por SSH bajo el alias 'homelab' (IP Tailscale: 100.98.35.64, ruta: /home/mrdavid/HomeLab).
- Para leer archivos, editar docker-compose.yml, revisar logs, levantar contenedores o hacer git push, ejecuta siempre comandos remotos usando: ssh homelab "comando".
- El directorio local `~/HomeLab-Agent` es un espejo de trabajo; la fuente de verdad es `/home/mrdavid/HomeLab` en el servidor.

## Contexto del Servidor
- **Hardware:** Dell OptiPlex 5040 con Debian Linux 12 y Docker Engine (4 vCPU, 8 GB RAM).
- **Almacenamiento:** Disco SSD de 228 GB (/dev/sda1 montado en /) con ~78 GB libres (64% de uso). Mantener siempre el espacio libre bajo monitoreo estricto.
- **Ruta Multimedia:** /home/mrdavid/HomeLab/media en el host, mapeada como /media en contenedores (/media/descargas, /media/peliculas, /media/series) con hardlinks funcionando para movimientos atómicos.
- **Volumen Crítico Nextcloud:** nextcloud_nextcloud_data (~118 GB).
- **Servicios Activos:**
  - Jellyfin (:8096 - network_mode host)
  - Seerr / Jellyseerr (:5055)
  - Radarr (:7878)
  - Sonarr (:8989)
  - Prowlarr (:9696)
  - FlareSolverr (:8191)
  - qBittorrent (:8081 WebUI, :6881 torrents) - API v2.11 / qBittorrent 5.3
  - Nextcloud (:8080)
  - Pi-hole v6 (:53 DNS, :80 Web Admin)
  - Portainer (:8000, :9000, :9443 HTTPS)
  - Optiplex Brain / Telegram Bot (optiplex-chatops - network_mode host, IPv4-first)

## Optiplex Brain (ChatOps Autónomo)
- Código: `/home/mrdavid/HomeLab/docker/telegram-bot` (`bot.js`, `config.js`, `lib/`, `modules/`).
- Motores: telemetría cada 60 s, autorreparación de contenedores (2 intentos, espera 20 s), centinela de torrents estancados, guardián de almacenamiento (35 GB preventivo / 20 GB crítico), bus de eventos media/red, vigilante de accesos externos y briefing diario a las 08:00 (`America/Santo_Domingo`).
- Consola Telegram: `/mission`, `/status`, `/containers`, `/queue`, `/buscar`, `/disk`, `/briefing`, `/iptv`, `/maint`, `/selftest`, `/help`; también acepta lenguaje natural y botones inline.
- Estado persistente: `/home/mrdavid/HomeLab/docker/telegram-bot/data/state.json` (ignorado por git).
- Prueba integral: `docker exec optiplex-chatops node --dns-result-order=ipv4first bot.js --selftest` (envía reporte real a Telegram).
- Reconstruir: `cd /home/mrdavid/HomeLab/docker/telegram-bot && docker compose up -d --build`.

## Restricciones Estrictas de Seguridad
1. **Protección de datos:** NUNCA borrar ni mover archivos dentro de /home/mrdavid/HomeLab/media (ni /media en contenedores), descargas activas de qBittorrent, ni volúmenes de Nextcloud sin confirmación explícita. El bot elimina torrents de qBittorrent sin borrar archivos.
2. **Protección de configuraciones:** NUNCA eliminar carpetas config, bases de datos (.db, .sqlite), ni archivos config.xml.
3. **Respaldo previo obligatorio:** Antes de modificar cualquier docker-compose.yml o estructura de carpetas, crear un respaldo con fecha en /home/mrdavid/backups-compose/.
4. **Privacidad en GitHub:** NUNCA subir archivos .env, credenciales, tokens, certificados, bases de datos, caché ni logs al repositorio. Mantener .gitignore rigurosamente actualizado.
5. **Acceso del bot:** El contenedor monta `/var/run/docker.sock` (equivalente a root en Docker) y `/:/hostfs:ro`. No exponerlo a Internet; solo responde al `CHAT_ID` autorizado y registra intentos externos.
6. **APIs:** Jellyfin requiere `Authorization: MediaBrowser Token="..."`; qBittorrent 5.3 exige parámetros POST en el cuerpo (`hashes=all`), no en la query; Pi-hole v6 usa `POST /api/auth` + `X-FTL-SID`.
