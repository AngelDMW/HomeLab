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
  - Jellyfin (:8096 - network_mode host) — QSV activo en `/dev/dri/renderD128` (H.264 encode/decode, HEVC 8-bit decode, MPEG-2/VC-1), tema ElegantFin + acento Netflix, plugins Media Bar 3.0.2.0 / File Transformation 3.0.3.0 / Intro Skipper 12.0.6.0 / TMDb Box Sets 15.0.0.0, metadatos `es-419`/`DO`
  - Threadfin (:34400 - `docker/threadfin`, TV en vivo para Android TV, separado de Jellyfin): imagen `fyb3roptik/threadfin:latest`, `restart: unless-stopped`, `512 MB`/`1.5 CPU`, volúmenes `./conf:/home/threadfin/conf` y `./temp:/tmp/threadfin`, modo `XEPG`, buffer `ffmpeg`. Playlists fuente: RD `https://iptv-org.github.io/iptv/countries/do.m3u`, ES `https://iptv-org.github.io/iptv/languages/spa.m3u`; EPG DobleM `https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml.gz`. URLs: M3U `http://192.168.1.3:34400/m3u/threadfin.m3u`, XMLTV `http://192.168.1.3:34400/xmltv/threadfin.xml`. Curaduría activa (45 canales vivos, probe Nodo 1 2026-11-18): 20 RD (1–20) + 25 INT ES (21–45), grupos `República Dominicana`/`Internacionales`, `Digital 15` excluido (403). Activación por alternativa controlada (XEPG auto no persistió en 1.2.37): salidas canónicas `conf/data/curated.m3u|.xml|.xml.gz` aplicadas con `/home/mrdavid/HomeLab/docker/threadfin/apply_curated.sh`. Tras reinicio/ciclo XEPG re-ejecutar el script para restaurar; M3U usa URLs directas (buffer ffmpeg aplica en proxy/stream). Backup conf: `/home/mrdavid/backups-compose/20261118_121126_threadfin_conf/conf`.
  - Seerr / Jellyseerr (:5055) — perfil predeterminado `1080p - Español Latino / Dual`, `syncEnabled: true`
  - Radarr (:7878) — perfiles IDs 7/8/9 y Custom Formats de idioma
  - Sonarr (:8989) — mismos perfiles y Custom Formats que Radarr
  - Bazarr (:6767) — subtítulos automáticos perfil `ea` + `es` + `ea:forced`, sync Radarr/Sonarr
  - Prowlarr (:9696)
  - FlareSolverr (:8191)
  - qBittorrent (:8081 WebUI, :6881 torrents) - API v2.11 / qBittorrent 5.3
  - Nextcloud (:8080)
  - Pi-hole v6 (:53 DNS, :80 Web Admin)
  - Portainer (:8000, :9000, :9443 HTTPS)
  - Optiplex Brain / Telegram Bot 2.1.0 (optiplex-chatops - network_mode host, IPv4-first)
  - Nodo 2: Docker listo, sin servicios permanentes (RX 550 VA-API + `/mnt/storage`)

## Topología de Red (actual)
- **Router:** ZTE F6201B en `192.168.1.1`; administración solo por `http://192.168.1.1:65080` (80/443 cerrados). DHCP activo con pool `.5`–`.249` (`.254` excluida para el AP). Radio 2.4 GHz apagado; 5 GHz con canal primario 36, 160 MHz, SSID `.I.`, WPA2-AES, WPS off. El firmware no expone el ancho de banda: permanece en 160 MHz y rechaza `BandWidth=80MHz`; 80 MHz no es configurable. Con 160 MHz el bloque 36–64 incluye subcanales DFS 52–64, así que la radio sigue expuesta a radar aunque el primario no sea DFS.
- **Servidor principal:** `server` con IP fija `192.168.1.3/24` en `enp0s31f6` (LAN4), gateway `.1`, enlace **1 Gbps**. Configuración en `/etc/network/interfaces`; **no volver a añadir el alias `192.168.1.2`**.
- **Nodo 2:** `homeLab-node2` con IP fija `192.168.1.4/24` en `enp3s0`, MAC `e0:d5:5e:02:70:aa`, gateway `.1`. Tailscale `100.67.30.11`. Alias SSH `homelab-node2`.
- **Binding DHCP:** entradas `.3` y `.4` en el ZTE, pero el firmware **no las aplica** (verificado). La IP fija real está en `/etc/network/interfaces` de cada nodo; el pool empieza en `.5` y termina en `.249` para no colisionar con el AP.
- **DHCP DNS:** primario y secundario `192.168.1.3` (Pi-hole), ISP DNS desactivado. **IPv6:** el RA del ZTE anuncia `fe80::1` como DNS; no se puede cambiar desde la UI y los clientes sin Tailscale pueden fugar consultas por IPv6.
- **Pi-hole:** `192.168.1.3:53` TCP/UDP y web `:80`; upstream `8.8.8.8`/`8.8.4.4`; lista StevenBlack (~77k dominios); sin DHCP propio.
- **Wi-Fi / otros:** AP Nexxt Amp300 `ARN02304U8` en `192.168.1.254` (no es el ZTE): bridge, solo 2.4 GHz, canal 6, 20 MHz, WPA2-AES, WPS y UPnP off. Troncal Fast Ethernet 100 Mbps; LAN de los nodos a 1 Gbps. Wi-Fi medido ~51–65 Mbps (5 muestras; mejor caso 64.5). Administración y PSK rotadas el 2026-11-18; credenciales en `~/HomeLab-Agent/.env.ap` (modo 600, sin versionar; nunca imprimir ni copiar al repo). El SSID `.I.` usa la misma PSK en el AP 2.4 GHz y en el ZTE 5 GHz; los clientes con la clave anterior deben reconectarse.
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

## Stack Multimedia y Perfiles de Idioma (2026-11-18)
- **Perfiles exactos en Radarr y Sonarr (IDs 7/8/9):**
  - `1080p - Español Latino / Dual` — predeterminado de Seerr; `LATINO` +1000, `DUAL` +700, Castellano +800, subs ES +100, `MULTI` 0, `IDIOMA NO ESPAÑOL (Dual/MULTI)` -2500 y `SOLO INGLÉS` -2000. Los CF `ES:`/`EN:` puntúan 0 (se basan en el título y confunden `ES-EN`). **`minFormatScore +500`**: nunca descarga solo-inglés.
  - `1080p - Original (Inglés) + Subs ES` — idioma de perfil `Original`, prioriza inglés y subtítulos ES, cutoff `Remux-1080p`.
  - `4K / 1080p - Dual / Latino (Máxima Calidad)` — incluye 2160p, mismo `minFormatScore +500`, cutoff `Remux-2160p`.
- **Custom Formats compartidos:** `LATINO`, `DUAL`, `MULTI`, `ESPAÑOL (Castellano)`, `SUBTÍTULOS ESPAÑOL`, `AUDIO INGLÉS`, `SOLO INGLÉS (sin español)`, `ES: Audio Español (detectado)`, `EN: Audio Inglés (detectado)`, `IDIOMA NO ESPAÑOL (Dual/MULTI)`. Radarr exige que **todos** los CF existentes estén presentes en cada perfil (aunque sea con score 0).
- **Seerr/Jellyseerr:** `syncEnabled: true` en Radarr y Sonarr; `activeProfileId` 7 y nombre `1080p - Español Latino / Dual`. El modal de solicitud muestra los 3 perfiles para elegir idioma por pedido.
- **Cómo pedir:** en `http://192.168.1.3:5055`, buscar título → **Solicitar** → elegir perfil (`Español Latino / Dual`, `Original + Subs ES` o `4K/1080p Máxima Calidad`) → confirmar. Radarr/Sonarr descargan y Bazarr añade subtítulos.
- **Bazarr (`:6767`):** perfil `Español Latino + Español + Forzados` con items `ea` normal, `es` normal y `ea` forzado. Los perfiles viven en SQLite; `serie_default_profile`/`movie_default_profile` deben ser el **ID entero** (1), no el nombre, o el sync falla con `FOREIGN KEY constraint failed`. Proveedores sin auth activos: argenteam, subscenter, subtitulamostv, subs4free, subs4series, podnapisi, subf2m, yifysubtitles, tvsubtitles, addic7ed, subtitlecat, supersubtitles. `subf2m`/`addic7ed` pueden auto-throttle si falta User-Agent o credenciales. Tras cambios de perfil, ejecutar `movies_full_scan_subtitles`/`series_full_scan_subtitles` para recalcular `missing_subtitles`; luego `wanted_search_missing_subtitles_*`.
- **Indexadores (2026-11-18):** en Radarr/Sonarr están Knaben, The Pirate Bay, LimeTorrents, YTS y `extratorrent-st`; `1337x` usa FlareSolverr con tag `flaresolverr` y `requestTimeout` 90 s. `torrentproject2` solo vive en Prowlarr (categoría `Other`, no se sincroniza). No hay definiciones públicas exclusivamente españolas: la cobertura latina depende de esos indexers. TPB limita a ~100 resultados y puede ocultar releases latinos de 1 seeder; en ese caso usar magnet + qBittorrent + **Manual Import** en Radarr/Sonarr, sin bajar `minFormatScore`. **Batman (2022)** sigue sin archivo: el `Dual latino` de TPB tiene 1 seeder inactivo y los `SPANiSH` de dem3nt3 no tienen seeds reales; queda monitoreada para auto-descarga cuando haya una versión sembrada.
- **Jellyfin:** QSV `HardwareAccelerationType=qsv`, dispositivo `/dev/dri/renderD128`, decodificación `h264`, `hevc` 8-bit, `mpeg2video`, `vc1`; H.264 encode por hardware. **HEVC encode y HEVC 10-bit/VP9 decode fallan en la iGPU Skylake** y deben quedar desactivados. Plugins: Media Bar 3.0.2.0 (requiere File Transformation 3.0.3.0), Intro Skipper 12.0.6.0, TMDb Box Sets 15.0.0.0. Repos: `https://www.iamparadox.dev/jellyfin/plugins/manifest.json` y `https://intro-skipper.org/manifest.json`.
- **Audio por defecto:** usuario `Mrdavid` con `AudioLanguagePreference=spa` y `PlayDefaultAudioTrack=true` (`POST /Users/Configuration?userId=<id>`). `RememberAudioSelections=true` conserva cambios manuales por ítem.
- **Tema Jellyfin:** CSS personalizado vía `POST /System/Configuration/Branding` con `CustomCss`; importa ElegantFin upstream, el add-on de Media Bar y el companion `mihaif7/elegantfin-jf12` para el layout Modern. Verificar con `GET /Branding/Css`.
- **Metadatos:** bibliotecas `Movies` (`/media/peliculas`) y `Series` (`/media/series`) con `PreferredMetadataLanguage=es-419` y `MetadataCountryCode=DO`. Refrescos completos vía `POST /Items/{id}/Refresh?metadataRefreshMode=FullRefresh&imageRefreshMode=FullRefresh`.
- **Backup de referencia:** `/home/mrdavid/backups-compose/20261118_170101_multimedia_pre_mejoras/`.

## Restricciones Estrictas de Seguridad
1. **Protección de datos:** NUNCA borrar ni mover archivos dentro de /home/mrdavid/HomeLab/media (ni /media en contenedores), descargas activas de qBittorrent, ni volúmenes de Nextcloud sin confirmación explícita. El bot elimina torrents de qBittorrent sin borrar archivos.
2. **Protección de configuraciones:** NUNCA eliminar carpetas config, bases de datos (.db, .sqlite), ni archivos config.xml.
3. **Respaldo previo obligatorio:** Antes de modificar cualquier docker-compose.yml, estructura de carpetas o configuración de red (`/etc/network/interfaces`), crear un respaldo con fecha en /home/mrdavid/backups-compose/ y, si el cambio puede cortar SSH, programar un rollback automático.
4. **Nodo 2 — discos:** NUNCA tocar el SSD Kingston del sistema. El único disco de datos es el HDD Toshiba montado por UUID en `/mnt/storage`; las letras `/dev/sdX` pueden intercambiarse entre reinicios, así que usa siempre UUID o la etiqueta `storage`.
5. **Nodo 2 — apagado remoto:** el apagado corta SSH y Tailscale. Verificar `Wake-on: g` en `enp3s0` antes de apagar y usar WoL para recuperarlo; no apagar el Nodo 2 sin una vía de encendido confirmada.
6. **Privacidad en GitHub:** NUNCA subir archivos .env, credenciales, tokens, certificados, bases de datos, caché ni logs al repositorio. Mantener .gitignore rigurosamente actualizado.
7. **Acceso del bot:** El contenedor monta `/var/run/docker.sock` (equivalente a root en Docker) y `/:/hostfs:ro`. No exponerlo a Internet; solo responde al `CHAT_ID` autorizado y registra intentos externos. La clave `node2_bot` es exclusiva del bot y se monta read-only.
8. **APIs:** Jellyfin requiere `Authorization: MediaBrowser Token="..."`; qBittorrent 5.3 exige parámetros POST en el cuerpo (`hashes=all`), no en la query; Pi-hole v6 usa `POST /api/auth` + `X-FTL-SID`.
