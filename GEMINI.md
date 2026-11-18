# Agente Autónomo del HomeLab

## Regla Principal de Ejecución
- Todo el proyecto y los servicios viven en el servidor remoto conectado por SSH bajo el alias 'homelab' (IP Tailscale: 100.98.35.64, ruta: /home/mrdavid/HomeLab).
- Para leer archivos, editar docker-compose.yml, revisar logs, levantar contenedores o hacer git push, ejecuta siempre comandos remotos usando: ssh homelab "comando".
- El directorio local `~/HomeLab-Agent` es un espejo de trabajo; la fuente de verdad es `/home/mrdavid/HomeLab` en el servidor.
- El Nodo 2 se administra con el alias `homelab-node2` (`192.168.1.4`, Tailscale `100.67.30.11`, usuario `mrdavid`). No tiene repositorio propio: el código y la documentación siguen viviendo en el Nodo 1.

## Contexto del Servidor
- **Hardware:** Dell OptiPlex 5040 con Debian Linux 12 y Docker Engine (4 vCPU, 8 GB RAM).
- **Almacenamiento:** Disco SSD de 228 GB (/dev/sda1 montado en /) con ~78 GB libres (64% de uso). Mantener siempre el espacio libre bajo monitoreo estricto.
- **Ruta Multimedia:** /home/mrdavid/HomeLab/media en el host, mapeada como /media en contenedores (/media/descargas, /media/peliculas, /media/series) con hardlinks funcionando para movimientos atómicos.
- **Volumen Crítico Nextcloud:** nextcloud_nextcloud_data (~118 GB).
- **Nodo 2:** AMD FX-8300 (8 núcleos), 3.8 GiB RAM, GPU Radeon RX 550 4 GB, Debian 12 (kernel 6.1.0-56), Docker Engine 29.10.0 + Compose v5.8.0. Sistema en SSD Kingston 223.6 GB; datos en HDD Toshiba 1 TB (`/mnt/storage`, ext4, UUID `1061ca8e-7ede-4c97-a487-1dcfed351446`, `nofail`). **No tocar el SSD Kingston.**
- **GPU Nodo 2:** VA-API operativo (Mesa 22.3.6): H.264 encode/decode, HEVC Main/Main10 decode, JPEG y MPEG-2/VC-1.
- **WoL Nodo 2:** `Wake-on: g` persistente en `enp3s0` vía `wol-enp3s0.service`; MAC `e0:d5:5e:02:70:aa`.
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
  - Optiplex Brain / Telegram Bot 2.1.0 (optiplex-chatops - network_mode host, IPv4-first)
  - Nodo 2: Docker listo, sin servicios permanentes (RX 550 VA-API + `/mnt/storage`)

## Topología de Red (actual)
- **Router:** ZTE F6201B en `192.168.1.1`; administración solo por `http://192.168.1.1:65080` (80/443 cerrados). DHCP activo con pool `.5`–`.254`.
- **Servidor principal:** `server` con IP fija `192.168.1.3/24` en `enp0s31f6` (LAN4), gateway `.1`, enlace **1 Gbps**. Configuración en `/etc/network/interfaces`; **no volver a añadir el alias `192.168.1.2`**.
- **Nodo 2:** `homeLab-node2` con IP fija `192.168.1.4/24` en `enp3s0`, MAC `e0:d5:5e:02:70:aa`, gateway `.1`. Tailscale `100.67.30.11`. Alias SSH `homelab-node2`.
- **Binding DHCP:** entradas `.3` y `.4` en el ZTE, pero el firmware **no las aplica** (verificado). La IP fija real está en `/etc/network/interfaces` de cada nodo; el pool empieza en `.5` para no colisionar.
- **DHCP DNS:** primario `192.168.1.3` (Pi-hole), secundario `1.1.1.1`, ISP DNS desactivado.
- **Pi-hole:** `192.168.1.3:53` TCP/UDP y web `:80`; upstream `8.8.8.8`/`8.8.4.4`; lista StevenBlack (~77k dominios); sin DHCP propio.
- **Wi-Fi / otros:** AP Huawei (SSID5); `192.168.1.254` es un dispositivo Nexxt con web GoAhead, no el ZTE.
- **Tailscale:** Nodo 1 `100.98.35.64`, Nodo 2 `100.67.30.11`; `/etc/resolv.conf` lo gestiona Tailscale (`100.100.100.100`), no Pi-hole.
- **Acceso remoto:** `tailscale serve --bg http://127.0.0.1:8081` publica `https://server.tailbd32c4.ts.net` **solo para la tailnet**. **Funnel desactivado** (`sudo tailscale funnel reset`): no exponer puertos a internet público. Verificar con `sudo tailscale serve status` (debe indicar `tailnet only`) y `sudo tailscale funnel status`.
- **Wake-on-LAN:** Nodo 2 `enp3s0` con `Wake-on: g` persistente; el bot envía magic packet a `.255`/broadcast en puertos 9 y 7.
- **Diagnóstico:** Nodo 1 `sudo ethtool enp0s31f6`, `ip -4 -br addr show enp0s31f6`, `dig @192.168.1.3`; Nodo 2 `ssh homelab-node2 'ip -4 -br addr show enp3s0; sudo ethtool enp3s0 | grep Wake-on; findmnt /mnt/storage'`.
- **Acceso remoto al router:** `ssh -L 65080:192.168.1.1:65080 homelab` y abrir `http://localhost:65080`.

## Optiplex Brain (ChatOps Autónomo)
- Versión 2.1.0. Código: `/home/mrdavid/HomeLab/docker/telegram-bot` (`bot.js`, `config.js`, `lib/`, `modules/`).
- Motores: telemetría cada 60 s, autorreparación de contenedores (2 intentos, espera 20 s), centinela de torrents estancados, guardián de almacenamiento (35 GB preventivo / 20 GB crítico), bus de eventos media/red, vigilante de accesos externos y briefing diario a las 08:00 (`America/Santo_Domingo`).
- Consola Telegram: `/mission`, `/status`, `/containers`, `/queue`, `/buscar`, `/disk`, `/node2`, `/briefing`, `/iptv`, `/maint`, `/selftest`, `/help`; también acepta lenguaje natural y botones inline.
- Control Nodo 2: `/node2` (estado), `/node2 on` (WoL), `/node2 off` (apagado con confirmación). Clave dedicada `/home/mrdavid/.ssh/node2_bot` montada en `/app/ssh/id_ed25519`; `known_hosts` en `data/known_hosts`. Variables `NODE2_*` en `.env` (`NODE2_HOST=192.168.1.4`, `NODE2_TAILSCALE_HOST=100.67.30.11`, `NODE2_MAC=e0:d5:5e:02:70:aa`).
- Estado persistente: `/home/mrdavid/HomeLab/docker/telegram-bot/data/state.json` (ignorado por git).
- Prueba integral: `docker exec optiplex-chatops node --dns-result-order=ipv4first bot.js --selftest` (envía reporte real a Telegram; 10 integraciones, incluido Nodo 2).
- Reconstruir: `cd /home/mrdavid/HomeLab/docker/telegram-bot && docker compose up -d --build`.

## Restricciones Estrictas de Seguridad
1. **Protección de datos:** NUNCA borrar ni mover archivos dentro de /home/mrdavid/HomeLab/media (ni /media en contenedores), descargas activas de qBittorrent, ni volúmenes de Nextcloud sin confirmación explícita. El bot elimina torrents de qBittorrent sin borrar archivos.
2. **Protección de configuraciones:** NUNCA eliminar carpetas config, bases de datos (.db, .sqlite), ni archivos config.xml.
3. **Respaldo previo obligatorio:** Antes de modificar cualquier docker-compose.yml, estructura de carpetas o configuración de red (`/etc/network/interfaces`), crear un respaldo con fecha en /home/mrdavid/backups-compose/ y, si el cambio puede cortar SSH, programar un rollback automático.
4. **Nodo 2 — discos:** NUNCA tocar el SSD Kingston del sistema. El único disco de datos es el HDD Toshiba montado por UUID en `/mnt/storage`; las letras `/dev/sdX` pueden intercambiarse entre reinicios, así que usa siempre UUID o la etiqueta `storage`.
5. **Nodo 2 — apagado remoto:** el apagado corta SSH y Tailscale. Verificar `Wake-on: g` en `enp3s0` antes de apagar y usar WoL para recuperarlo; no apagar el Nodo 2 sin una vía de encendido confirmada.
6. **Privacidad en GitHub:** NUNCA subir archivos .env, credenciales, tokens, certificados, bases de datos, caché ni logs al repositorio. Mantener .gitignore rigurosamente actualizado.
7. **Acceso del bot:** El contenedor monta `/var/run/docker.sock` (equivalente a root en Docker) y `/:/hostfs:ro`. No exponerlo a Internet; solo responde al `CHAT_ID` autorizado y registra intentos externos. La clave `node2_bot` es exclusiva del bot y se monta read-only.
8. **APIs:** Jellyfin requiere `Authorization: MediaBrowser Token="..."`; qBittorrent 5.3 exige parámetros POST en el cuerpo (`hashes=all`), no en la query; Pi-hole v6 usa `POST /api/auth` + `X-FTL-SID`.
