# HomeLab Server - Dell OptiPlex 5040

Infraestructura self-hosted sobre Debian Linux y Docker para nube privada, bloqueo DNS y streaming multimedia automatizado.

## Hardware y Red
- Equipo: Dell OptiPlex 5040 (Debian Linux + Docker)
- Almacenamiento: ~88 GB optimizados con limite de calidad
- Red: Router ZTE + AP Huawei | IP Local: 192.168.1.3 | Tailscale: 100.98.35.64

## Servicios Docker
- Infraestructura: Nextcloud, Portainer, Pi-hole, Tailscale
- Jellyfin (:8096): Servidor multimedia y TV en Vivo (IPTV)
- Seerr (:5055): Portal de solicitud de peliculas y series
- Radarr (:7878) y Sonarr (:8989): Automatizacion de peliculas y series
- Prowlarr (:9696) y FlareSolverr (:8191): Indexadores y bypass Cloudflare
- qBittorrent (:8081): Cliente BitTorrent

## Flujo de Calidad y TV en Vivo
- Idioma: Custom Format Dual/Latino/Spanish (1000 pts) con minimo 0 y respaldo en ingles
- Espacio: Perfil HD-1080p limitado a max 4.5 GiB/h en WEBDL y Bluray
- Live TV: Listas M3U (do.m3u y spa.m3u) + guia XMLTV y favoritos familiares

## Estructura
- docker/media-stack/docker-compose.yml
- .gitignore
- README.md
- Telegram Bot (docker/telegram-bot): Notificaciones y control remoto
