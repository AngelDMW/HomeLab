# HomeLab Server - Dell OptiPlex 5040

Infraestructura self-hosted de alta disponibilidad sobre Debian Linux 12 y Docker Engine, diseñada para nube privada, bloqueo y filtrado DNS en red, streaming multimedia totalmente automatizado y ChatOps asistido por IA.

---

## 🖥️ Especificaciones de Hardware y Red

| Parámetro | Detalle |
| :--- | :--- |
| **Equipo** | Dell OptiPlex 5040 SFF |
| **Sistema Operativo** | Debian GNU/Linux 12 (Bookworm) x86_64 |
| **Kernel** | Linux 6.1.0-53-amd64 |
| **Almacenamiento** | Disco SSD 228 GB (/dev/sda1) | ~81 GB libres (63% en uso) |
| **Memoria RAM** | 8 GB DDR3L / DDR4 | Swap: 1 GB |
| **Red Local** | `192.168.1.3` (Router ZTE + AP Huawei) |
| **Red Mesh VPN** | Tailscale `100.98.35.64` (alias `homelab`) |

---

## 📦 Inventario de Servicios Docker

### 1. Nube Privada e Infraestructura Base
- **Nextcloud** (`:8080`): Nube privada personal para sincronización de archivos, galería y respaldos móviles. Volumen de datos dedicado: `nextcloud_nextcloud_data` (~119 GB).
- **Pi-hole** (`:53`, `:80`): Servidor DNS sinkhole para bloqueo centralizado de anuncios y telemetría en toda la red doméstica.
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
- **OptiPlex ChatOps** (`docker/telegram-bot`, `network_mode: host`): Bot de Telegram con integración a LLM (Llama 3 8B vía HuggingFace Inference) para ejecución supervisada de comandos remotos, diagnósticos y alertas operativas forzado en modo IPv4-first.

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
│   ├── telegram-bot/         # Bot autónomo ChatOps para Telegram
│   │   ├── Dockerfile
│   │   ├── bot.js
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

## ⚡ Hardlinks y Movimientos Atómicos

Para evitar duplicar espacio en disco en la partición SSD:
1. Todos los servicios de descarga y gestión (`qbittorrent`, `radarr`, `sonarr`, `jellyfin`) montan la misma ruta raíz `/home/mrdavid/HomeLab/media` en el contenedor bajo `/media`.
2. Al completarse una descarga en `/media/descargas`, Radarr/Sonarr crean un **hardlink** (enlace duro apuntando al mismo inodo) en `/media/peliculas` o `/media/series`.
3. Esto permite seguir sembrando el torrent en qBittorrent sin consumir doble almacenamiento en el disco.

---

## 🔒 Seguridad y Privacidad

- **Gestión de Secretos:** Ningún archivo `.env`, token de bot, llave API ni contraseña se sube a GitHub. Cada carpeta contiene su respectivo `.env.example`.
- **Bases de Datos y Caché:** Todas las carpetas `config`, cachés de transcodificación y bases de datos (`.db`, `.sqlite`) están excluidas en `.gitignore`.
- **Respaldos Automatizados:** Todo cambio en composiciones o configuraciones crea un snapshot previo en `/home/mrdavid/backups-compose/YYYYMMDD_HHMMSS/`.
