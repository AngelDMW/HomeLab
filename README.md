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
| **Gateway / Router** | ZTE F6201B · `192.168.1.1` · administración `http://192.168.1.1:65080` · DHCP activo (`.5`–`.254`) |
| **Servidor principal** | `server` · `192.168.1.3/24` fija · MAC `48:4d:7e:f2:5a:a2` · puerto LAN4 a 1 Gbps |
| **Nodo 2** | `homeLab-node2` · `192.168.1.4/24` fija · MAC `e0:d5:5e:02:70:aa` · `enp3s0` |
| **DHCP Binding** | Reservas `.3` y `.4` en el ZTE. **El firmware F6201B V9.3.10P4N10B no aplicó las reservas** (verificado); ambos nodos usan IP estática en `/etc/network/interfaces` y el pool dinámico empieza en `.5` para evitar colisiones. |
| **DHCP DNS** | Primario `192.168.1.3` (Pi-hole) · secundario `1.1.1.1` · ISP DNS desactivado |
| **Pi-hole** | DNS `192.168.1.3:53` TCP/UDP · web `http://192.168.1.3/admin/` · upstream `8.8.8.8`, `8.8.4.4` |
| **Wi-Fi** | AP Huawei / SSID5 · los servidores van por cable, no por Wi-Fi |
| **Dispositivo .254** | Nexxt Solutions con web GoAhead (no es el router ZTE) |
| **Wake-on-LAN** | Nodo 2: magic packet al MAC `e0:d5:5e:02:70:aa`; `Wake-on: g` persistente en `enp3s0` |
| **Acceso remoto** | Tailscale Serve `https://server.tailbd32c4.ts.net` → qBittorrent `127.0.0.1:8081`, **solo dentro de la tailnet**; Funnel desactivado (sin exposición a internet) |

La configuración de red de ambos nodos está en `/etc/network/interfaces` como IP estática. Pi-hole mantiene su propio DHCP desactivado: el ZTE sigue siendo el servidor DHCP y anuncia a Pi-hole como DNS primario.

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
- **Jellyfin** (`:8096`, `network_mode: host`): Servidor multimedia con transcodificación por hardware (`/dev/dri`) y gestión de canales IPTV en vivo (listas M3U + XMLTV).
- **Seerr / Jellyseerr** (`:5055`): Portal de descubrimiento y solicitudes de contenido para usuarios.
- **Radarr** (`:7878`): Automatización, búsqueda y gestión de películas.
- **Sonarr** (`:8989`): Automatización, búsqueda y gestión de series de TV.
- **Prowlarr** (`:9696`): Administrador e integrador de indexadores de torrents.
- **FlareSolverr** (`:8191`): Servidor proxy para elusión de protecciones Cloudflare anti-bot en indexadores.
- **qBittorrent** (`:8081` WebUI, `:6881` Peers): Cliente BitTorrent optimizado con interfaz web.

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
│   ├── media-stack/          # Stack Radarr, Sonarr, Prowlarr, qBittorrent, Jellyseerr, Flaresolverr
│   │   ├── docker-compose.yml
│   │   └── .env.example
│   ├── jellyfin/             # Servidor multimedia Jellyfin (host networking + DRI)
│   │   └── docker-compose.yml
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
