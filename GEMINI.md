# Agente Autónomo del HomeLab

## Regla Principal de Ejecución
- Todo el proyecto y los servicios viven en el servidor remoto conectado por SSH bajo el alias 'homelab' (IP Tailscale: 100.98.35.64, ruta: /home/mrdavid/HomeLab).
- Para leer archivos, editar docker-compose.yml, revisar logs, levantar contenedores o hacer git push, ejecuta siempre comandos remotos usando: ssh homelab "comando".

## Contexto del Servidor
- **Hardware:** Dell OptiPlex 5040 con Debian Linux 12 y Docker Engine.
- **Almacenamiento:** Disco SSD de 228 GB (/dev/sda1 montado en /) con ~81 GB libres (63% de uso). Mantener siempre el espacio libre bajo monitoreo estricto.
- **Ruta Multimedia:** /home/mrdavid/HomeLab/media en el host, mapeada como /media en contenedores (/media/descargas, /media/peliculas, /media/series) con hardlinks / enlaces duros funcionando para movimientos atómicos.
- **Volumen Crítico Nextcloud:** nextcloud_nextcloud_data (~119 GB).
- **Servicios Activos:**
  - Jellyfin (:8096 - network_mode host)
  - Seerr / Jellyseerr (:5055)
  - Radarr (:7878)
  - Sonarr (:8989)
  - Prowlarr (:9696)
  - FlareSolverr (:8191)
  - qBittorrent (:8081 WebUI, :6881 torrents)
  - Nextcloud (:8080)
  - Pi-hole (:53 DNS, :80 Web Admin)
  - Portainer (:8000, :9000, :9443 HTTPS)
  - Telegram Bot (optiplex-chatops - network_mode host, IPv4-first)

## Restricciones Estrictas de Seguridad
1. **Protección de datos:** NUNCA borrar ni mover archivos dentro de /home/mrdavid/HomeLab/media (ni /media en contenedores), descargas activas de qBittorrent, ni volúmenes de Nextcloud sin confirmación explícita.
2. **Protección de configuraciones:** NUNCA eliminar carpetas config, bases de datos (.db, .sqlite), ni archivos config.xml.
3. **Respaldo previo obligatorio:** Antes de modificar cualquier docker-compose.yml o estructura de carpetas, crear un respaldo con fecha en /home/mrdavid/backups-compose/.
4. **Privacidad en GitHub:** NUNCA subir archivos .env, credenciales, tokens, certificados, bases de datos, caché ni logs al repositorio. Mantener .gitignore rigurosamente actualizado.
