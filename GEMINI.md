# Agente Autonomo del HomeLab

## Regla Principal de Ejecucion
- Todo el proyecto y los servicios viven en el servidor remoto conectado por SSH bajo el alias 'homelab' (IP Tailscale: 100.98.35.64, ruta: /home/mrdavid/HomeLab).
- Para leer archivos, editar docker-compose.yml, revisar logs, levantar contenedores o hacer git push, ejecuta siempre comandos remotos usando: ssh homelab "comando".

## Contexto del Servidor
- Hardware: Dell OptiPlex 5040 con Debian Linux y Docker.
- Almacenamiento: Particion de ~88 GB (critico cuidar el espacio libre).
- Servicios activos: Jellyfin (:8096), Seerr (:5055), Radarr (:7878), Sonarr (:8989), Prowlarr (:9696), FlareSolverr (:8191), qBittorrent (:8081), Nextcloud, Portainer, Pi-hole y docker/telegram-bot.
- Restricciones estrictas: Nunca borrar archivos dentro de /media sin confirmacion explicita. Nunca subir config.xml, bases de datos, cache ni .env a GitHub.
