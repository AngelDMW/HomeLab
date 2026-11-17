# HomeLab Server - Dell OptiPlex 5040

Infraestructura self-hosted de alta disponibilidad sobre Debian Linux 12 y Docker Engine, diseñada para nube privada, bloqueo y filtrado DNS en red, streaming multimedia totalmente automatizado y un agente ChatOps autónomo (Optiplex Brain) con telemetría, autorreparación y consola interactiva por Telegram.

---

## 🖥️ Especificaciones de Hardware y Red

| Parámetro | Detalle |
| :--- | :--- |
| **Equipo** | Dell OptiPlex 5040 SFF |
| **Sistema Operativo** | Debian GNU/Linux 12 (Bookworm) x86_64 |
| **Kernel** | Linux 6.1.0-53-amd64 |
| **Almacenamiento** | Disco SSD 228 GB (/dev/sda1) | ~78 GB libres (64% en uso) |
| **Memoria RAM** | 8 GB DDR3L / DDR4 | Swap: 1 GB |
| **Red Local** | `192.168.1.3/24` fija (binding DHCP en ZTE F6201B; LAN4 a 1 Gbps) |
| **Red Mesh VPN** | Tailscale `100.98.35.64` (alias `homelab`) |

### Topología de red

| Elemento | Detalle |
| :--- | :--- |
| **Gateway / Router** | ZTE F6201B · `192.168.1.1` · administración `http://192.168.1.1:65080` · DHCP activo (`.2`–`.254`) |
| **Servidor** | `server` · `192.168.1.3/24` fija · MAC `48:4d:7e:f2:5a:a2` · puerto LAN4 a 1 Gbps |
| **DHCP Binding** | Reserva MAC `48:4d:7e:f2:5a:a2` → `192.168.1.3` en el ZTE |
| **DHCP DNS** | Primario `192.168.1.3` (Pi-hole) · secundario `1.1.1.1` · ISP DNS desactivado |
| **Pi-hole** | DNS `192.168.1.3:53` TCP/UDP · web `http://192.168.1.3/admin/` · upstream `8.8.8.8`, `8.8.4.4` |
| **Wi-Fi** | AP Huawei / SSID5 · el servidor va por cable, no por Wi-Fi |
| **Dispositivo .254** | Nexxt Solutions con web GoAhead (no es el router ZTE) |
| **Acceso remoto** | Tailscale Serve `https://server.tailbd32c4.ts.net` → qBittorrent `127.0.0.1:8081`, **solo dentro de la tailnet**; Funnel desactivado (sin exposición a internet) |

La configuración del servidor está en `/etc/network/interfaces` como IP estática, sin el antiguo alias `192.168.1.2`. Pi-hole mantiene su propio DHCP desactivado: el ZTE sigue siendo el servidor DHCP y anuncia a Pi-hole como DNS primario.

#### Verificación rápida

```bash
sudo ethtool enp0s31f6 | grep -E "Speed|Duplex"   # 1000Mb/s / Full
ip -4 -br addr show enp0s31f6                     # solo 192.168.1.3/24
dig +short @192.168.1.3 github.com
dig +short @192.168.1.3 doubleclick.net           # 0.0.0.0
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
- **Optiplex Brain** (`docker/telegram-bot`, `network_mode: host`): agente modular en Node.js 18 con polling IPv4-first. Integra telemetría cada 60 s, autorreparación de contenedores, guardián de almacenamiento, bus de eventos multimedia/red y consola interactiva por Telegram con botones inline y lenguaje natural. Solo responde al `CHAT_ID` autorizado.

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
│   │   ├── modules/          # Telemetría, Docker, Servarr, qBittorrent, guardianes
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
- `/briefing`, `/iptv`, `/maint`, `/selftest`, `/help`.
- Lenguaje natural: “estado del servidor”, “reinicia sonarr”, “pausa las descargas”, “busca Cars”, “¿cuánto espacio libre queda?”.
- Los botones de limpieza solo purgan capas huérfanas y build cache. La eliminación de un torrent conserva siempre los archivos descargados.

### Montajes y modelo de seguridad

- `/var/run/docker.sock` (rw): necesario para inspeccionar y reiniciar contenedores. Equivale a acceso root sobre Docker; el contenedor no se expone a Internet y solo obedece al `CHAT_ID` del propietario.
- `/:/hostfs:ro`: permite leer `/proc`, `/sys` y `/var/lib/docker` del host para telemetría y desglose de disco sin modificarlo.
- `./data:/app/data`: estado persistente (incidentes, hashes notificados, offsets de logs).

### Operación

```bash
cd /home/mrdavid/HomeLab/docker/telegram-bot
docker compose up -d --build          # reconstruir y recrear
docker compose logs -f --tail=100     # observar el arranque
docker exec optiplex-chatops node --dns-result-order=ipv4first bot.js --selftest
```

El autodiagnóstico valida Telegram, Docker, host, Radarr, Sonarr, Seerr, Jellyfin, qBittorrent y Pi-hole, y envía el reporte real al Telegram del propietario. Antes de cada cambio se crea un respaldo en `/home/mrdavid/backups-compose/`.

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
- **Datos protegidos:** El bot nunca borra ni mueve archivos de `/media` ni volúmenes de Nextcloud. “Eliminar torrent” solo lo quita de qBittorrent y conserva los archivos.
- **Respaldos Automatizados:** Todo cambio en composiciones o configuraciones crea un snapshot previo en `/home/mrdavid/backups-compose/YYYYMMDD_HHMMSS/`.
