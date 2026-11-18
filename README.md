# HomeLab Multi-Nodo — Dell OptiPlex 5040 + Nodo 2

Infraestructura self-hosted de alta disponibilidad sobre Debian Linux 12 y Docker Engine, distribuida en dos nodos: el servidor principal (Dell OptiPlex 5040) y un nodo secundario con GPU para cómputo y almacenamiento. Incluye nube privada, bloqueo y filtrado DNS en red, streaming multimedia automatizado y un agente ChatOps autónomo (Optiplex Brain 2.1.0) con telemetría, autorreparación, control multi-nodo y consola interactiva por Telegram.

---

## 🖥️ Especificaciones de Hardware y Red

### Nodo 1 — Dell OptiPlex 5040 (`server`)

| Parámetro | Detalle |
| :--- | :--- |
| **Equipo** | Dell OptiPlex 5040 SFF |
| **Sistema Operativo** | Debian GNU/Linux 12 (Bookworm) x86_64 |
| **Kernel** | Linux 6.1.0-53-amd64 |
| **Almacenamiento** | Disco SSD 228 GB (/dev/sda1) | ~78 GB libres (64% en uso) |
| **Memoria RAM** | 8 GB DDR3L / DDR4 | Swap: 1 GB |
| **Red Local** | `192.168.1.3/24` fija (LAN4 a 1 Gbps) |
| **Red Mesh VPN** | Tailscale `100.98.35.64` (alias `homelab`) |

### Nodo 2 — `homeLab-node2`

| Parámetro | Detalle |
| :--- | :--- |
| **Equipo** | PC de escritorio AMD FX-8300 |
| **Sistema Operativo** | Debian GNU/Linux 12 (Bookworm) x86_64 |
| **Kernel** | Linux 6.1.0-56-amd64 |
| **CPU** | AMD FX-8300 Eight-Core (8 núcleos) |
| **Memoria RAM** | 3.8 GiB (~4 GB) |
| **GPU** | AMD Radeon RX 550 4 GB (Polaris 12) · VA-API con Mesa 22.3.6 |
| **Sistema** | SSD Kingston SA400S37240G 223.6 GB · ext4 en `/` |
| **Datos** | HDD Toshiba DT01ABA100V 1 TB · ext4 etiqueta `storage` · `/mnt/storage` |
| **Red Local** | `192.168.1.4/24` fija en `enp3s0` · MAC `e0:d5:5e:02:70:aa` |
| **Red Mesh VPN** | Tailscale `100.67.30.11` |
| **Acceso** | SSH `mrdavid@192.168.1.4` · alias local `homelab-node2` |
| **Docker** | Docker Engine 29.10.0 · Compose v5.8.0 · usuario `mrdavid` en grupo `docker` |
| **Wake-on-LAN** | `enp3s0` con `Wake-on: g` persistente (`wol-enp3s0.service`) |

> El HDD Toshiba se monta por **UUID** (`1061ca8e-7ede-4c97-a487-1dcfed351446`) con `nofail`; las letras `/dev/sdX` pueden intercambiarse entre reinicios (en el arranque actual el Kingston es `sda` y el Toshiba `sdb`). No tocar el SSD Kingston: contiene el sistema operativo. La GPU tiene VA-API operativo para H.264 encode/decode, HEVC Main/Main10 decode, JPEG y MPEG-2/VC-1.

### Topología de red

| Elemento | Detalle |
| :--- | :--- |
| **Gateway / Router** | ZTE F6201B · `192.168.1.1` · administración `http://192.168.1.1:65080` · DHCP activo (`.5`–`.249`; `.254` excluida para el AP) |
| **Servidor principal** | `server` · `192.168.1.3/24` fija · MAC `48:4d:7e:f2:5a:a2` · puerto LAN4 a 1 Gbps |
| **Nodo 2** | `homeLab-node2` · `192.168.1.4/24` fija · MAC `e0:d5:5e:02:70:aa` · `enp3s0` |
| **DHCP Binding** | Reservas `.3` y `.4` en el ZTE. **El firmware F6201B V9.3.10P4N10B no aplicó las reservas** (verificado); ambos nodos usan IP estática en `/etc/network/interfaces` y el pool dinámico empieza en `.5` para evitar colisiones. |
| **DHCP DNS** | Primario y secundario `192.168.1.3` (Pi-hole) · ISP DNS desactivado · **IPv6: el RA del ZTE anuncia `fe80::1` como DNS y no es configurable desde la UI; los clientes sin Tailscale pueden fugar consultas por IPv6** |
| **Pi-hole** | DNS `192.168.1.3:53` TCP/UDP · web `http://192.168.1.3/admin/` · upstream `8.8.8.8`, `8.8.4.4` |
| **Wi-Fi** | AP Nexxt Amp300 `ARN02304U8` · `192.168.1.254` · bridge, solo 2.4 GHz, canal **6**, **20 MHz**, **WPA2-AES**, WPS y UPnP off · puertos Fast Ethernet (troncal 100 Mbps) · admin y PSK rotados (credenciales en `~/HomeLab-Agent/.env.ap` del equipo de administración, modo 600, fuera del repo) |
| **Wi-Fi ZTE** | Radio 2.4 GHz **apagado**; 5 GHz activo con canal primario **36**, **160 MHz** (el bloque 36–64 incluye subcanales DFS 52–64), SSID `.I.`, WPA2-AES, PSK compartida con el AP |
| **Rendimiento** | LAN cableada Nodo2→Nodo1 **~926 Mbps**; Wi-Fi Mac→Nodo1 **~51–65 Mbps** (PHY 144 Mbps); WAN **84 Mbps** con bufferbloat bajo |
| **Nota .254** | El AP Nexxt no es el router ZTE. Su administración y su PSK se rotaron el **2026-11-18**; las credenciales viven en `~/HomeLab-Agent/.env.ap` del equipo de administración (modo 600, sin versionar). Solo WPA2, sin WPA3. |
| **Wake-on-LAN** | Nodo 2: magic packet al MAC `e0:d5:5e:02:70:aa`; `Wake-on: g` persistente en `enp3s0` |
| **Acceso remoto** | Tailscale Serve `https://server.tailbd32c4.ts.net` → qBittorrent `127.0.0.1:8081`, **solo dentro de la tailnet**; Funnel desactivado (sin exposición a internet) |

La configuración de red de ambos nodos está en `/etc/network/interfaces` como IP estática. Pi-hole mantiene su propio DHCP desactivado: el ZTE sigue siendo el servidor DHCP y anuncia a Pi-hole como DNS primario y secundario. El AP Nexxt está en modo bridge (sin DHCP ni NAT propios); su troncal es Fast Ethernet, por lo que el Wi-Fi nunca superará ~100 Mbps aunque el enlace del AP al ZTE negocie 100/Full.

El SSID `.I.` se comparte entre el 5 GHz del ZTE y el 2.4 GHz del AP con la **misma PSK**, de modo que el roaming entre radios no falla por credenciales. Tras la rotación del 2026-11-18, los clientes que conserven la clave anterior deben reconectarse una vez. El 5 GHz quedó con canal primario 36, pero el firmware lo mantiene en 160 MHz: la UI no expone el selector de ancho y el POST con `80MHz` es rechazado, así que **80 MHz no es configurable** en este ZTE. Con 160 MHz el bloque 36–64 incluye los subcanales DFS 52–64, por lo que la radio sigue expuesta a eventos radar aunque el canal primario no sea DFS. La Mac quedó en el AP 2.4 GHz (canal 6, 20 MHz, PHY 144 Mbps) y el cliente 5 GHz `.6` volvió a asociarse en 80 MHz/11ax.

#### Verificación rápida

```bash
sudo ethtool enp0s31f6 | grep -E "Speed|Duplex"   # 1000Mb/s / Full
ip -4 -br addr show enp0s31f6                     # solo 192.168.1.3/24
dig +short @192.168.1.3 github.com
dig +short @192.168.1.3 doubleclick.net           # 0.0.0.0

ssh homelab-node2 'ip -4 -br addr show enp3s0'    # 192.168.1.4/24
ssh homelab-node2 'sudo ethtool enp3s0 | grep Wake-on'          # g
ssh homelab-node2 'findmnt -no SOURCE,TARGET,FSTYPE /mnt/storage'  # ext4
ssh homelab-node2 'vainfo | grep -E "Driver version|H264High|HEVCMain"'
```

---

## 📦 Inventario de Servicios Docker

### 1. Nube Privada e Infraestructura Base
- **Nextcloud** (`:8080`): Nube privada personal para sincronización de archivos, galería y respaldos móviles. Volumen de datos dedicado: `nextcloud_nextcloud_data` (~119 GB).
- **Pi-hole** (`:53`, `:80`): Servidor DNS sinkhole para bloqueo centralizado de anuncios y telemetría en toda la red doméstica. El DHCP del ZTE anuncia `192.168.1.3` como DNS primario; Pi-hole reenvía a `8.8.8.8`/`8.8.4.4` y mantiene la lista StevenBlack.
- **Portainer CE** (`:8000`, `:9000`, `:9443`): Panel web de administración y monitoreo de contenedores y volúmenes Docker.

### 2. Stack Multimedia Automatizado (Servarr + Media)
- **Jellyfin** (`:8096`, `network_mode: host`): Servidor multimedia con **Intel QuickSync (QSV)** activo sobre `/dev/dri/renderD128` (H.264 encode/decode, HEVC 8-bit decode, MPEG-2/VC-1 decode), tema ElegantFin + acentos Netflix, Media Bar, Intro Skipper y TMDb Box Sets. Metadatos en `es-419`/`DO`.
- **Threadfin** (`:34400`, `docker/threadfin`): proxy/gestor de TV en vivo para Android TV, **separado de Jellyfin**. Imagen `fyb3roptik/threadfin:latest`, `restart: unless-stopped`, límite `512 MB` / `1.5 CPU`, TZ `America/Santo_Domingo`. Volúmenes persistentes: `./conf` → `/home/threadfin/conf` y `./temp` → `/tmp/threadfin`. EPG en modo `XEPG`, buffer `ffmpeg` habilitado (global y por playlist).
  - **Playlists fuente:** República Dominicana `https://iptv-org.github.io/iptv/countries/do.m3u` (262 streams) y Español `https://iptv-org.github.io/iptv/languages/spa.m3u` (2441 streams).
  - **EPG XMLTV:** `https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml.gz` (DobleM, español), asociada vía `url-tvg`.
  - **URLs para Android TV:** M3U `http://192.168.1.3:34400/m3u/threadfin.m3u` · XMLTV `http://192.168.1.3:34400/xmltv/threadfin.xml`.
  - **Curaduría activa (45 canales, verificados vivos desde Nodo 1 con `curl` timeout 5s el 2026-11-18):** 20 `República Dominicana` (1–20) + 25 `Internacionales` en español (21–45), nombres limpios, numeración secuencial desde 1. `Digital 15` excluido (403 en probe).
  - **Activación (alternativa controlada):** la activación automática XEPG no persistió en Threadfin 1.2.37 (cleanup regeneraba `xepg.json` vacío/0 canales). Se sirven salidas curadas canónicas (`conf/data/curated.m3u|.xml|.xml.gz`) aplicadas a `threadfin.m3u|.xml|.xml.gz` con `/home/mrdavid/HomeLab/docker/threadfin/apply_curated.sh`.
  - **Implicación de actualización:** Threadfin regenera las salidas en cada arranque/ciclo XEPG. Tras reiniciar el contenedor o una actualización XEPG, ejecutar `ssh homelab 'bash /home/mrdavid/HomeLab/docker/threadfin/apply_curated.sh'` para restaurar la curaduría. La M3U curada usa URLs directas de origen (los endpoints M3U/XMLTV quedan operativos; buffer ffmpeg de Threadfin aplica en modo proxy/stream). Fuentes/EPG originales permanecen en `conf/data/` para re-curar. Backup conf previo: `/home/mrdavid/backups-compose/20261118_121126_threadfin_conf/conf`.
- **Seerr / Jellyseerr** (`:5055`): Portal de descubrimiento y solicitudes de contenido para usuarios.
- **Radarr** (`:7878`): Automatización, búsqueda y gestión de películas.
- **Sonarr** (`:8989`): Automatización, búsqueda y gestión de series de TV.
- **Bazarr** (`:6767`, `lscr.io/linuxserver/bazarr:latest`): Subtítulos automáticos multi-idioma. Perfil activo **“Español Latino + Español + Forzados”** (`ea`, `es`, `ea:forced`). Sincronizado con Radarr y Sonarr; proveedores sin autenticación: `argenteam`, `subscenter`, `subtitulamostv`, `subs4free`, `subs4series`, `podnapisi`, `subf2m`, `yifysubtitles`, `tvsubtitles`, `addic7ed`, `subtitlecat`, `supersubtitles`. Los subtítulos se guardan junto al video (`<archivo>.<idioma>.srt`).
- **Prowlarr** (`:9696`): Administrador e integrador de indexadores de torrents.
- **FlareSolverr** (`:8191`): Servidor proxy para elusión de protecciones Cloudflare anti-bot en indexadores.
- **qBittorrent** (`:8081` WebUI, `:6881` Peers): Cliente BitTorrent optimizado con interfaz web.

#### Perfiles de idioma y calidad (2026-11-18)

Radarr y Sonarr comparten tres perfiles con nombres exactos (IDs `7`, `8`, `9`), cada uno con Custom Formats por título de release y por idioma detectado:

| Perfil | Uso | Prioridad de puntuación |
| :--- | :--- | :--- |
| `1080p - Español Latino / Dual` | **Predeterminado en Seerr**. Busca 720p/1080p con audio latino, dual o multi. | Latino `+1000`, Dual `+700`, Multi `+500`, Castellano `+300`, subs ES `+100`; solo inglés `-2000`. `minFormatScore -1000`, cutoff `Bluray-1080p` con 500 puntos. |
| `1080p - Original (Inglés) + Subs ES` | Para quien prefiere audio original y subtítulos en español. | Inglés `+200/+300`, subs ES `+300`, latino `-100`. Idioma de perfil `Original`, cutoff `Remux-1080p`. |
| `4K / 1080p - Dual / Latino (Máxima Calidad)` | Máxima calidad, incluye 2160p Remux/Bluray/WEB. | Misma prioridad latino/dual; cutoff `Remux-2160p`. |

Los Custom Formats creados en ambos Servarr son: `LATINO`, `DUAL`, `MULTI`, `ESPAÑOL (Castellano)`, `SUBTÍTULOS ESPAÑOL`, `AUDIO INGLÉS`, `SOLO INGLÉS (sin español)`, `ES: Audio Español (detectado)` y `EN: Audio Inglés (detectado)`.

#### Cómo solicitar una película o serie y elegir idioma (Seerr)

1. Entrar a **http://192.168.1.3:5055** e iniciar sesión.
2. Buscar el título y abrir su ficha; pulsar **Solicitar**.
3. En el desplegable **Perfil de calidad** elegir:
   - **`1080p - Español Latino / Dual`** → audio latino/dual (recomendado para ver doblado).
   - **`1080p - Original (Inglés) + Subs ES`** → audio original + subtítulos en español (Bazarr los descarga).
   - **`4K / 1080p - Dual / Latino (Máxima Calidad)`** → mejor calidad disponible, incluye 4K.
4. Confirmar la solicitud. Radarr/Sonarr buscan, qBittorrent descarga y el archivo aparece por hardlink en Jellyfin.
5. Bazarr descarga automáticamente subtítulos latino/español y forzados; Jellyfin los muestra en el selector de subtítulos durante la reproducción.

> El perfil predeterminado de Seerr es `1080p - Español Latino / Dual`; si el usuario no cambia nada, la solicitud sale con esa intención. La disponibilidad real de audio latino depende de los indexadores (Knaben, The Pirate Bay, LimeTorrents, YTS).

#### Jellyfin — experiencia tipo streaming

- **Tema:** ElegantFin + companion para el layout Modern de Jellyfin 12 + soporte de Media Bar, aplicado como CSS personalizado desde el panel. Acentos rojos tipo Netflix, tarjetas redondeadas, header translúcido y hero cinematográfico.
- **Plugins activos:** Media Bar `3.0.2.0` (hero con backdrop, logo, sinopsis, play y tráilers), File Transformation `3.0.3.0` (requerido por Media Bar), Intro Skipper `12.0.6.0` (detecta intro/créditos y usa Media Segments nativos), TMDb Box Sets `15.0.0.0` (colecciones automáticas).
- **Metadatos:** bibliotecas `Movies` y `Series` en `es-419` con país `DO`; títulos, sinopsis, géneros, backdrops y logos se piden a TMDb/OMDb en español.
- **QuickSync:** `HardwareAccelerationType=qsv`, dispositivo `/dev/dri/renderD128`, decodificación `h264`, `hevc` (8-bit), `mpeg2video`, `vc1`; encoding H.264 por hardware. HEVC encode y decodificación 10-bit/VP9 **no** están soportados por la iGPU Skylake y quedan desactivados para evitar fallbacks silenciosos.

### 3. Asistente Autónomo (ChatOps)
- **Optiplex Brain 2.1.0** (`docker/telegram-bot`, `network_mode: host`): agente modular en Node.js 18 con polling IPv4-first. Integra telemetría cada 60 s, autorreparación de contenedores, guardián de almacenamiento, bus de eventos multimedia/red, control multi-nodo del Nodo 2 (estado, Wake-on-LAN y apagado) y consola interactiva por Telegram con botones inline y lenguaje natural. Solo responde al `CHAT_ID` autorizado.

### 4. Nodo 2 — Capacidad de Reserva
- **Docker Engine 29.10.0 + Compose v5.8.0** instalados y activos, sin cargas de trabajo permanentes.
- **GPU RX 550** con VA-API listo para futuros servicios de transcodificación (H.264 encode/decode, HEVC Main/Main10 decode, JPEG, MPEG-2/VC-1).
- **HDD Toshiba 1 TB** (`/mnt/storage`, ext4) para datos; el SSD Kingston queda reservado al sistema operativo.

---

## 📂 Arquitectura de Carpetas y Volúmenes

```text
/home/mrdavid/HomeLab/
├── docker/
│   ├── media-stack/          # Stack Radarr, Sonarr, Bazarr, Prowlarr, qBittorrent, Jellyseerr, Flaresolverr
│   │   ├── docker-compose.yml
│   │   ├── bazarr/           # Config/DB de Bazarr (perfil es-419 + es + forzados)
│   │   └── .env.example
│   ├── jellyfin/             # Servidor multimedia Jellyfin (host networking + QSV)
│   │   ├── docker-compose.yml
│   │   └── config/           # Configuración, plugins y DB de Jellyfin (ignorado por git)
│   ├── threadfin/            # TV en vivo (Threadfin) separada de Jellyfin
│   │   ├── docker-compose.yml
│   │   ├── conf/             # Configuración y base Threadfin (persistente)
│   │   └── temp/             # Caché/buffer temporal
│   ├── telegram-bot/         # Optiplex Brain (agente autónomo ChatOps)
│   │   ├── Dockerfile
│   │   ├── bot.js            # Entrada, comandos, callbacks y autocontrol
│   │   ├── config.js         # Configuración y umbrales desde .env
│   │   ├── lib/              # HTTP, formato, logger y estado persistente
│   │   ├── modules/          # Telemetría, Docker, Servarr, qBittorrent, Nodo 2, guardianes
│   │   ├── data/             # Estado runtime (ignorado por git)
│   │   ├── package.json
│   │   ├── docker-compose.yml
│   │   └── .env.example
│   ├── pihole/               # Definición IaC de Pi-hole
│   │   ├── docker-compose.yml
│   │   └── .env.example
│   ├── nextcloud/            # Definición IaC de Nextcloud
│   │   └── docker-compose.yml
│   └── portainer/            # Definición IaC de Portainer CE
│       └── docker-compose.yml
├── media/                    # Almacenamiento raíz compartido (Host: /home/mrdavid/HomeLab/media -> Contenedor: /media)
│   ├── descargas/            # Torrents activos e incompletos de qBittorrent
│   ├── peliculas/            # Librería final de películas (Radarr)
│   └── series/               # Librería final de series (Sonarr)
├── docs/                     # Diagramas y documentación arquitectónica
├── .gitignore                # Reglas estrictas de exclusión de datos y secretos
├── GEMINI.md                 # Contexto y directrices operativas del Agente de IA
└── README.md                 # Documentación técnica del repositorio
```

---

## 🧠 Optiplex Brain — Agente Autónomo

El contenedor `optiplex-chatops` dejó de ser un bot de comandos y ahora es un ecosistema modular de vigilancia y operación.

### Motores internos

| Motor | Función | Frecuencia |
| :--- | :--- | :--- |
| **Telemetry & Heartbeat** | CPU, temperatura (`Package id 0`), RAM, carga, uptime, red/Tailscale y disco | Bajo demanda y muestreo cada 60 s |
| **Self-Healing** | Detecta contenedores `exited`/`unhealthy`, reinicia hasta 2 veces, espera 20 s, verifica estado y puerto, y alerta con los últimos 15 logs si persiste | 60 s |
| **Torrent Sentinel** | Detecta `stalledDL` sin seeds durante más de 6 h y ofrece botones para buscar otro release, eliminar el torrent o ignorarlo | 10 min |
| **Storage Guardian** | Alerta preventiva con < 35 GB libres, crítica con < 20 GB (pausa qBittorrent) y reanudación automática al recuperar 40 GB | 5 min |
| **Live Event Bus** | Nuevas descargas, idioma detectado, fin de descarga, solicitudes Seerr, caídas/reconexiones de internet y Tailscale | 60 s |
| **Security Watcher** | Conexiones establecidas a Nextcloud/Portainer desde IPs no privadas e inicios de sesión externos en `nextcloud.log` | 60 s |
| **Morning Briefing** | Reporte ejecutivo diario con sistemas, disco, media importada en 24 h y Pi-hole | 08:00 `America/Santo_Domingo` |

### Consola de Telegram

- `/mission` · `/status` — panel principal con telemetría y botones inline.
- `/containers` — estado de la flota y reinicio de cualquier servicio con un toque.
- `/queue` — progreso de qBittorrent, pausar/reanudar y solicitudes de Seerr.
- `/buscar <título>` — busca películas en Seerr y permite solicitarlas con un toque.
- `/disk` — desglose de consumo (Nextcloud, media, Docker).
- `/node2` — estado del Nodo 2, encenderlo por Wake-on-LAN y apagarlo con confirmación.
- `/briefing`, `/iptv`, `/maint`, `/selftest`, `/help`.
- Lenguaje natural: “estado del servidor”, “estado del nodo 2”, “enciende el nodo 2”, “apaga el nodo 2”, “reinicia sonarr”, “pausa las descargas”, “busca Cars”, “¿cuánto espacio libre queda?”.
- Los botones de limpieza solo purgan capas huérfanas y build cache. La eliminación de un torrent conserva siempre los archivos descargados.

### Control del Nodo 2

El bot se conecta por SSH con una clave dedicada (`/home/mrdavid/.ssh/node2_bot`, montada read-only en `/app/ssh/id_ed25519`) y usa `NODE2_HOST=192.168.1.4` con fallback a Tailscale `100.67.30.11`. El `known_hosts` propio se guarda en `data/known_hosts`.

| Acción | Cómo | Efecto |
| :--- | :--- | :--- |
| **Estado** | `/node2` o botón `🖥️ Nodo 2` | hostname, OS, kernel, CPU, RAM, discos, GPU, Docker, uptime e IPs |
| **Encender** | `/node2 on` o botón `⚡ Encender (WoL)` | magic packet a `192.168.1.255` y `255.255.255.255`, puertos 9 y 7 |
| **Apagar** | `/node2 off` → confirmar | `sudo -n poweroff` vía SSH; requiere `Wake-on: g` para volver a encender |

Variables en `.env`: `NODE2_NAME`, `NODE2_HOST`, `NODE2_TAILSCALE_HOST`, `NODE2_MAC`, `NODE2_SSH_USER`, `NODE2_SSH_PORT`, `NODE2_SSH_KEY`, `NODE2_WOL_BROADCAST` y `NODE2_STORAGE_PATH`.

### Montajes y modelo de seguridad

- `/var/run/docker.sock` (rw): necesario para inspeccionar y reiniciar contenedores. Equivale a acceso root sobre Docker; el contenedor no se expone a Internet y solo obedece al `CHAT_ID` del propietario.
- `/:/hostfs:ro`: permite leer `/proc`, `/sys` y `/var/lib/docker` del host para telemetría y desglose de disco sin modificarlo.
- `./data:/app/data`: estado persistente (incidentes, hashes notificados, offsets de logs, `known_hosts`).
- `/home/mrdavid/.ssh/node2_bot:/app/ssh/id_ed25519:ro`: clave SSH dedicada del bot hacia el Nodo 2, con permisos `600`. No se reutiliza la clave personal del administrador.

### Operación

```bash
cd /home/mrdavid/HomeLab/docker/telegram-bot
docker compose up -d --build          # reconstruir y recrear
docker compose logs -f --tail=100     # observar el arranque
docker exec optiplex-chatops node --dns-result-order=ipv4first bot.js --selftest
```

El autodiagnóstico valida Telegram, Docker, host, Radarr, Sonarr, Seerr, Jellyfin, qBittorrent, Pi-hole y Nodo 2 (10 integraciones), y envía el reporte real al Telegram del propietario. Antes de cada cambio se crea un respaldo en `/home/mrdavid/backups-compose/`.

---

## ⚡ Hardlinks y Movimientos Atómicos

Para evitar duplicar espacio en disco en la partición SSD:
1. Todos los servicios de descarga y gestión (`qbittorrent`, `radarr`, `sonarr`, `jellyfin`) montan la misma ruta raíz `/home/mrdavid/HomeLab/media` en el contenedor bajo `/media`.
2. Al completarse una descarga en `/media/descargas`, Radarr/Sonarr crean un **hardlink** (enlace duro apuntando al mismo inodo) en `/media/peliculas` o `/media/series`.
3. Esto permite seguir sembrando el torrent en qBittorrent sin consumir doble almacenamiento en el disco.

---

## 🔒 Seguridad y Privacidad

- **Gestión de Secretos:** Ningún archivo `.env`, token de bot, llave API ni contraseña se sube a GitHub. Cada carpeta contiene su respectivo `.env.example`.
- **Bases de Datos y Caché:** Todas las carpetas `config`, cachés de transcodificación y bases de datos (`.db`, `.sqlite`) están excluidas en `.gitignore`.
- **Acceso al bot:** Solo el `CHAT_ID` del propietario puede ejecutar comandos o pulsar botones; cualquier otro mensaje se ignora y se registra en los logs.
- **Acceso al Nodo 2:** clave SSH dedicada `node2_bot` (sin passphrase, permisos `600`) autorizada solo para `mrdavid`; el bot no toca el SSD Kingston ni formatea discos. El apagado remoto exige confirmación explícita y solo se usa con `Wake-on: g` verificado.
- **Datos protegidos:** El bot nunca borra ni mueve archivos de `/media` ni volúmenes de Nextcloud. “Eliminar torrent” solo lo quita de qBittorrent y conserva los archivos.
- **Respaldos Automatizados:** Todo cambio en composiciones o configuraciones crea un snapshot previo en `/home/mrdavid/backups-compose/YYYYMMDD_HHMMSS/`.
