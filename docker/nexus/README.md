# HomeLab Nexus

> Centro de mando personal del HomeLab: lanzador unificado de servicios, bóveda cifrada de credenciales y panel de acciones rápidas, todo en una sola aplicación web instalable.

**HomeLab Nexus** sustituye la costumbre de saltar entre marcadores, hojas de cálculo y notas sueltas por un único punto de entrada. Desde el móvil o el escritorio se abre un panel que conoce la topología de la red, cambia entre la Casa (LAN) y el acceso remoto (Tailscale), comprueba el estado real de los servicios y guarda las credenciales cifradas en disco. Incorpora además **🏢 Oficina IA (HomeLab Corp Live)**: una vista animada en tiempo real con el Director y los 8 empleados IA trabajando sobre datos vivos del ledger de HomeLab Corp y de Sentinel AI.

| Ficha rápida | Valor |
|---|---|
| URL en la LAN | `http://192.168.1.3:8888` |
| URL por Tailscale | `http://100.98.35.64:8888` |
| Contenedor | `homelab-nexus` |
| Stack | Python 3.11 · FastAPI · Uvicorn · SQLite (WAL) |
| Datos | `./data/nexus.sqlite3` (cifrado a nivel de campo) |
| Memoria límite | 192 MB |
| Puerto interno | `8888/tcp` |
| Arranque | `restart: unless-stopped` |
| Healthcheck | `GET /health` cada 30 s |

---

## Índice

1. [Qué es HomeLab Nexus](#1-qué-es-homelab-nexus)
2. [Arquitectura](#2-arquitectura)
3. [Funciones principales](#3-funciones-principales)
4. [Los 19 servicios del clúster](#4-los-19-servicios-del-clúster)
5. [Instalación como app nativa](#5-instalación-como-app-nativa)
6. [Puesta en marcha](#6-puesta-en-marcha)
7. [Operación diaria](#7-operación-diaria)
8. [Respaldos y restauración](#8-respaldos-y-restauración)
9. [Seguridad](#9-seguridad)
10. [Referencia de la API](#10-referencia-de-la-api)
11. [Solución de problemas](#11-solución-de-problemas)
12. [Mapa de archivos](#12-mapa-de-archivos)

---

## 1. Qué es HomeLab Nexus

Nexus es una **PWA** (aplicación web progresiva) que se instala en la pantalla de inicio del iPhone o en el Dock del Mac y se comporta como una app nativa: ventana propia, icono propio, sin barra de navegador y sin depender de la App Store.

Resuelve tres problemas concretos del día a día:

- **Dispersión de accesos.** Los 19 servicios oficiales del clúster (multimedia, nube, DNS, Docker, SSH, IA) viven en un solo tablero con favoritos, categorías y filtros.
- **Cambio de red manual.** Cada servicio tiene dos direcciones (LAN y Tailscale). Un interruptor conmuta todas las tarjetas a la vez, sin editar marcadores ni recordar qué IP toca.
- **Credenciales en texto plano.** Las contraseñas se guardan cifradas con Fernet, se revelan solo bajo sesión autenticada y el portapapeles se limpia solo a los 30 segundos.

Además incluye un **panel de acciones** con runbooks listos para ejecutar o copiar, y una **bitácora técnica** persistente para dejar constancia de cambios, incidencias y decisiones.

---

## 2. Arquitectura

```
Navegador (PWA)
   │  cookie HttpOnly nexus_session
   ▼
FastAPI + Uvicorn  :8888
   ├── app/auth.py     sesiones y credenciales de acceso
   ├── app/crypto.py   cifrado Fernet de contraseñas
   ├── app/db.py       SQLite en modo WAL
   ├── app/seed.py     catálogo inicial de 19 servicios + runbooks
   ├── app/status.py   sondas HTTP/TCP en paralelo
   └── app/static/     interfaz, service worker y manifest PWA
   ▼
./data/nexus.sqlite3   (volumen persistente, ignorado por Git)
```

Decisiones de diseño relevantes:

- **Contenedor único.** No hay base de datos externa ni proxy: menos superficie, menos piezas que reiniciar.
- **SQLite con WAL.** Lecturas concurrentes sin bloquear la interfaz; `busy_timeout` de 5 s y un lock de escritura en proceso.
- **Cifrado a nivel de campo.** Solo el campo `encrypted_password` se guarda cifrado; el resto (nombres, URLs, notas) queda legible en la base de datos. La UI nunca recibe la contraseña en el listado: solo un booleano `has_password`.
- **Todo local.** No hay telemetría, CDN ni llamadas a terceros. La única salida de red son las comprobaciones de estado y los runbooks HTTP que se configuren.
- **Interfaz sin framework.** HTML, CSS y JavaScript vanilla, con estética *glassmorphism* oscura y `prefers-reduced-motion` respetado.

---

## 3. Funciones principales

### 3.1 Conmutador inteligente de red (Casa ↔ Tailscale)

El interruptor del encabezado alterna entre dos modos:

| Modo | Prefijo | Uso |
|---|---|---|
| 🏠 **Red Casa LAN** | `192.168.1.x` | Estás en casa, en la misma red que el servidor |
| 🛡️ **Remoto Tailscale** | `100.x.y.z` | Estás fuera, conectado a la tailnet |

Cómo funciona:

- Cada servicio guarda `lan_url` y `vpn_url` por separado.
- Al cambiar de modo, las tarjetas se vuelven a pintar y los botones **Abrir Panel** apuntan a la URL correspondiente.
- La preferencia se guarda en `localStorage` (`nexus-network`), así que se conserva al cerrar y reabrir la app.
- Si un servicio no tiene URL para el modo activo, se usa la otra como respaldo. Los routers, por ejemplo, solo existen en LAN: en modo Tailscale seguirán mostrando su dirección local, que **no será alcanzable** desde fuera.
- El indicador lateral (punto verde/azul) refleja el modo activo, no la conectividad real de cada servicio.

> El conmutador no modifica la red del dispositivo ni las rutas del servidor: solo elige qué URL se muestra y se abre. La conectividad real depende de que Tailscale esté activo en el cliente.

### 3.2 Bóveda cifrada de credenciales

- **Cifrado en reposo.** Cada contraseña se cifra con Fernet. La clave se deriva de `NEXUS_MASTER_KEY` mediante SHA-256 y se codifica en base64 URL-safe.
- **Revelado bajo demanda.** El listado devuelve únicamente `has_password`. El texto plano solo se entrega en `POST /api/items/{id}/reveal`, que exige sesión válida.
- **Limpieza del portapapeles.** Al copiar una contraseña, la interfaz muestra el aviso «Copiado · se limpiará en 30 s» y programa el borrado del portapapeles a los 30 segundos.
- **Copia de usuario.** El nombre de usuario se copia sin temporizador (no es un secreto).
- **Face ID / Llavero de iCloud.** Nexus no implementa biometría propia: declara los campos con `autocomplete="username"` y `autocomplete="current-password"`, de modo que iOS y macOS ofrecen la credencial guardada en el Llavero y la desbloquean con Face ID o Touch ID. Es la vía recomendada para no teclear la contraseña maestra cada vez.
- **CRUD completo.** Crear, editar, eliminar, marcar favorito, asignar categoría e icono, y definir URL de health check por servicio.

> **Importante:** la limpieza automática usa la Clipboard API, disponible solo en *contextos seguros* (HTTPS o `localhost`). Si se abre Nexus por `http://192.168.1.3:8888`, la copia funciona mediante un fallback y **el borrado a los 30 s no se ejecuta**. Para la garantía completa, servir la app por HTTPS (ver §9).

### 3.3 Indicador de estado en vivo

- La interfaz llama a `GET /api/status-all` al cargar y **cada 60 segundos**; el botón de refrescar fuerza una comprobación inmediata.
- El backend lanza las sondas **en paralelo** con un timeout de 1,5 s por servicio.
- Tipos de sonda:
  - `http://` o `https://` → se considera **online** cualquier respuesta con código `< 500` (incluye 401/403: el servicio está vivo aunque pida login).
  - `tcp://host:puerto` o `ssh://usuario@host` → se considera **online** si el puerto acepta conexión.
  - Sin URL de health → se usa la URL LAN como respaldo.
- El resultado se muestra como punto verde (online), rojo (offline) o gris (sin estado aún) en cada tarjeta.
- Los certificados TLS autofirmados (Portainer) se aceptan: las sondas usan `verify=False`.

### 3.4 Buscador instantáneo y panel de comandos

- **Buscador global** en la barra superior. Filtra en tiempo real servicios, runbooks y notas por nombre, categoría, usuario, notas, URLs y contenido.
- **Atajo `Cmd + K`** (macOS) o **`Ctrl + K`** (Windows/Linux) para enfocar el buscador desde cualquier punto; `Esc` cierra el modal y quita el foco.
- **Acciones rápidas.** Dos tarjetas destacadas:
  - 🛡️ *Auditar Clúster* → `POST http://192.168.1.3:9090/api/audit` (Sentinel).
  - 🧠 *Convocar Director IA* → webhook `/webhook/director` de n8n en el Nodo 2.
- **Runbooks.** Once entradas iniciales en cinco categorías:
  - **Acciones** (HTTP ejecutable): auditoría de Sentinel y webhook del Director.
  - **SSH**: comandos de conexión a ambos nodos por LAN y por Tailscale.
  - **Logs**: logs de Nexus y de Sentinel.
  - **Diagnóstico** y **Reinicios seguros**: estado de contenedores y reconstrucción de Nexus.
- Los runbooks de tipo `copy` muestran el comando con un botón para copiarlo; los de tipo `http` se ejecutan desde el servidor y devuelven código y cuerpo de la respuesta (máximo 2 000 caracteres) en el panel de feedback.
- **Bitácora técnica.** Notas persistentes en SQLite con título, contenido, fecha de actualización y CRUD completo. Se siembra una nota inicial de bienvenida.

### 3.5 🏢 Oficina IA (HomeLab Corp Live)

Nueva pestaña disponible en la barra lateral de macOS y en la barra inferior de iPhone. Muestra a los **9 personajes** —`00 HomeLab-Director` + `Infra`, `SecOps`, `Media`, `Cloud`, `Updater`, `Backup`, `Architect` y `QA`— en sus estaciones de trabajo dentro del escenario **HomeLab Corp HQ**.

- **Datos reales, no simulados.** El backend consulta en paralelo (timeout 2 s, tolerante a fallos) `corp-tools-api` (`192.168.1.4:8006`: `/api/ledger/summary`, `/api/tools/backup/history`, `/api/tools/updater/history`, `/api/ledger/proposals`, `/api/tools/qa`) y Sentinel AI (`192.168.1.3:9090/api/state`). Cada personaje expone `state`, `bubble_text`, `last_report`, `qa_score`, `last_seen` y `active_links`.
- **Estados animados:** `idle_guard` (respiración en guardia), `working` (tecleo), `reporting` (informe), `healing` (pulso/naranja) y `alert` (pulso/rojo). Las partículas SVG recorren las líneas de datos del Director hacia la estación correspondiente cuando hay orders o escalados.
- **Burbujas en vivo** con métricas reales: uso de SSD y Nodo 2, DNSSEC/CrowdSec, versión de Jellyfin, respaldo verificado, propuestas pendientes, QA y sondas OK.
- **Controles:** indicador **EN VIVO · 4 s**, tareas completadas, último backup 3-2-1, estado de las sondas y botón **⚡ Convocar Ronda General** (POST al webhook `/webhook/director` de n8n, con confirmación previa).
- **Ficha al tocar:** biografía operativa, último informe completo del ledger, puntuación QA, enlaces activos y botón **Asignar revisión ahora**, que invoca solo el webhook predefinido del empleado (`/webhook/agent-<rol>`) en modo `review_only` (nunca dispara backup ni actualización real).
- **Ligereza:** toda la renderización es **SVG animado + CSS/JS del navegador**. No hay librerías gráficas ni dependencias Node nuevas; `homelab-nexus` sigue con `mem_limit: 192m` (consumo observado ~45 MB).
- **Móvil:** en pantallas ≤860 px el escenario SVG se sustituye por tarjetas táctiles con el mismo avatar, estado, burbuja y acceso a la ficha.
- **Seguridad:** los endpoints están tras la cookie HttpOnly de Nexus. `CORP_INTERNAL_TOKEN` solo se usa como cabecera `X-Corp-Token` hacia `corp-tools-api`; jamás se devuelve al navegador ni se escribe en logs o Git.

---

## 4. Los 19 servicios del clúster

El catálogo se siembra automáticamente la primera vez que arranca el contenedor (solo si la tabla está vacía). Estas son las 19 entradas oficiales:

| # | Servicio | Categoría | LAN | Tailscale | Health check |
|---|---|---|---|---|---|
| 1 | 🛡️ Sentinel Mission Control | IA & Control | `192.168.1.3:9090` | `100.98.35.64:9090` | `/health` |
| 2 | 🧠 n8n AI Corp | IA & Control | `192.168.1.4:5678` | `100.67.30.11:5678` | `/healthz` |
| 3 | 🤖 Bot Telegram | IA & Control | `t.me/ServerDMW_bot` | `t.me/ServerDMW_bot` | — |
| 4 | 🎬 HomeFlix (Seerr) | Multimedia | `192.168.1.3:5055` | `100.98.35.64:5055` | `/api/v1/status` |
| 5 | 📺 Jellyfin | Multimedia | `192.168.1.3:8096` | `100.98.35.64:8096` | `/health` |
| 6 | 📡 Threadfin TV | Multimedia | `192.168.1.3:34400` | `100.98.35.64:34400` | URL base |
| 7 | ⬇️ qBittorrent | Multimedia | `192.168.1.3:8081` | `100.98.35.64:8081` | URL base |
| 8 | 🎞️ Radarr | Multimedia | `192.168.1.3:7878` | `100.98.35.64:7878` | `/ping` |
| 9 | 📼 Sonarr | Multimedia | `192.168.1.3:8989` | `100.98.35.64:8989` | `/ping` |
| 10 | 💬 Bazarr | Multimedia | `192.168.1.3:6767` | `100.98.35.64:6767` | URL base |
| 11 | ☁️ Nextcloud | Nube & Fotos | `192.168.1.3:8080` | `100.98.35.64:8080` | `/status.php` |
| 12 | 🖼️ Immich Fotos | Nube & Fotos | `192.168.1.3:2283` | `100.98.35.64:2283` | `/api/server/ping` |
| 13 | 🕳️ Pi-hole DNS | Red & Seguridad | `192.168.1.3/admin` | `100.98.35.64/admin` | `/admin/` |
| 14 | 🐳 Portainer Nodo 1 | Red & Seguridad | `https://192.168.1.3:9443` | `https://100.98.35.64:9443` | `/api/system/status` |
| 15 | 🐳 Portainer Nodo 2 | Red & Seguridad | `192.168.1.4:9000` | `100.67.30.11:9000` | `/api/system/status` |
| 16 | 📶 Router Principal | Red & Seguridad | `192.168.1.1:65080` | — (solo LAN) | URL base |
| 17 | 📡 Router AP | Red & Seguridad | `192.168.1.254` | — (solo LAN) | URL base |
| 18 | 🖥️ SSH Nodo 1 | Infraestructura & SSH | `ssh://mrdavid@192.168.1.3` | `ssh://mrdavid@100.98.35.64` | `tcp://192.168.1.3:22` |
| 19 | 🧩 SSH Nodo 2 | Infraestructura & SSH | `ssh://mrdavid@192.168.1.4` | `ssh://mrdavid@100.67.30.11` | `tcp://192.168.1.4:22` |

Notas:

- Los elementos 1, 2, 3, 4, 5, 11 y 12 vienen marcados como **favoritos**.
- El ítem 17 se sembró como «Router AP Huawei»; el AP físico documentado del HomeLab es el Nexxt Amp300 en `192.168.1.254`. Puede renombrarse desde la propia interfaz.
- El ítem 15 y el 19 solo responden cuando el Nodo 2 está encendido. Si está apagado, aparecerán en rojo: es el comportamiento esperado.
- Los routers no se publican por Tailscale; en modo remoto hay que acceder a ellos por un túnel SSH (`ssh -L`).
- El catálogo es editable: se pueden añadir servicios propios, cambiar puertos o borrar entradas. La siembra **no se repite** mientras existan filas en `vault_items`.

---

## 5. Instalación como app nativa

Nexus se instala desde el navegador. No hay APK, DMG ni TestFlight.

### 5.1 iPhone / iPad (Safari)

1. Conectar el iPhone a la red de casa (o activar Tailscale si se va a usar en remoto).
2. Abrir **Safari** y navegar a `http://192.168.1.3:8888` (o a la URL de Tailscale).
3. Iniciar sesión una vez para confirmar que carga correctamente.
4. Tocar el botón **Compartir** (cuadrado con flecha hacia arriba) en la barra inferior.
5. Desplazarse y elegir **Añadir a pantalla de inicio**.
6. Cambiar el nombre a `Nexus` si se desea y pulsar **Añadir**.
7. Abrir el icono nuevo: se lanza en modo *standalone*, sin barra de Safari.

Recomendaciones:

- Instalar desde **Safari**. Otros navegadores en iOS pueden crear un marcador en lugar de una app independiente.
- Elegir un único origen (LAN **o** Tailscale) y quedarse con él. Si se instala desde la IP local y luego se abre desde la tailnet, será otro origen y no compartirá sesión ni credenciales guardadas.
- Para que Face ID autocomplete la contraseña maestra: **Ajustes → Contraseñas → Opciones de contraseña → Rellenar automáticamente** activado, y guardar la credencial en el Llavero de iCloud la primera vez.
- En iOS, la copia al portapapeles y el service worker requieren HTTPS. Por HTTP la app funciona, pero sin caché offline y sin borrado automático del portapapeles.

### 5.2 macOS (Chrome → Dock)

1. Abrir Chrome y navegar a `http://192.168.1.3:8888`.
2. Iniciar sesión.
3. Abrir el menú **⋮** (tres puntos, arriba a la derecha).
4. Entrar en **Transmitir, guardar y compartir** → **Instalar página como aplicación…**
   - En versiones anteriores: **Guardar y compartir** → **Crear acceso directo…** y marcar **Abrir como ventana**.
5. Confirmar el nombre `Nexus` y pulsar **Instalar**.
6. La app aparece en el Dock y en Launchpad. Se puede arrastrar para fijarla.

Alternativa con Safari en macOS Sonoma o posterior:

1. Abrir la URL en Safari.
2. Menú **Archivo → Añadir al Dock**.
3. Confirmar. Queda como app web en el Dock.

Para Touch ID: guardar la credencial en el Llavero de iCloud o en el gestor de contraseñas de Chrome y aceptar el autocompletado. Chrome pedirá Touch ID si está configurado en el Mac.

> **Consejo:** instalar la PWA apuntando a la URL de Tailscale (`http://100.98.35.64:8888`) hace que funcione igual en casa y fuera, siempre que Tailscale esté activo. La pega es que depende de la tailnet; la URL LAN solo sirve dentro de la red local.

---

## 6. Puesta en marcha

### 6.1 Requisitos

- Docker Engine y Docker Compose v2 en el Nodo 1.
- Puerto `8888` libre en el host.
- Acceso de lectura/escritura a `./data`.

### 6.2 Variables de entorno

Crear `docker/nexus/.env` (nunca se versiona). Plantilla con **marcadores**, sin valores reales:

```env
# Identidad del panel
NEXUS_USER=<usuario>
NEXUS_PASSWORD=<contraseña-maestra-fuerte>

# Clave de cifrado de la bóveda (independiente de la contraseña)
NEXUS_MASTER_KEY=<cadena-aleatoria-larga>

# Opcionales
NEXUS_SESSION_DAYS=14
NEXUS_DB_PATH=/app/data/nexus.sqlite3

# Oficina IA (HomeLab Corp Live) — NUNCA versionar el token
CORP_API_URL=http://192.168.1.4:8006
CORP_INTERNAL_TOKEN=<token-interno-de-corp-tools-api>
# CORP_DIRECTOR_URL=http://192.168.1.4:5678/webhook/director
# SENTINEL_URL=http://192.168.1.3:9090
```

| Variable | Obligatoria | Descripción |
|---|---|---|
| `NEXUS_USER` | Sí | Usuario del formulario de acceso |
| `NEXUS_PASSWORD` | Sí | Contraseña maestra del panel |
| `NEXUS_MASTER_KEY` | Sí | Semilla del cifrado Fernet. Si falta, el contenedor falla al sembrar la base |
| `NEXUS_SESSION_DAYS` | No | Duración de la sesión en días (por defecto `14`) |
| `NEXUS_DB_PATH` | No | Ruta de SQLite (por defecto `/app/data/nexus.sqlite3`) |
| `CORP_API_URL` | No | Base de `corp-tools-api` (por defecto `http://192.168.1.4:8006`) |
| `CORP_INTERNAL_TOKEN` | Sí* | Token interno enviado como `X-Corp-Token`. Copiar de `docker/ai-corp/.env`; sin él la oficina queda en modo degradado |
| `CORP_DIRECTOR_URL` | No | Webhook del Director (por defecto `http://192.168.1.4:5678/webhook/director`) |
| `SENTINEL_URL` | No | Base de Sentinel AI (por defecto `http://192.168.1.3:9090`) |

\* Obligatorio para los datos vivos de Oficina IA; el resto de Nexus sigue funcionando sin él.

Generar una clave maestra robusta:

```bash
openssl rand -base64 48 | tr '+/' '-_' | tr -d '='
```

Buenas prácticas:

```bash
cd /home/mrdavid/HomeLab/docker/nexus
umask 077
mkdir -p data
# crear/editar .env con un editor seguro
chmod 600 .env
chmod 700 data
```

- `NEXUS_MASTER_KEY` **no** se cambia a la ligera: si se modifica, las contraseñas ya cifradas dejan de poder descifrarse y el sistema devuelve cadena vacía.
- Guardar una copia de `NEXUS_MASTER_KEY` y del `.env` en el gestor de contraseñas. Sin esa clave, un respaldo de la base de datos no sirve para recuperar las contraseñas.

### 6.3 Arranque

```bash
cd /home/mrdavid/HomeLab/docker/nexus
docker compose up -d --build
```

Verificación:

```bash
docker ps --filter name=homelab-nexus
curl -fsS http://127.0.0.1:8888/health
# {"ok":true,"service":"homelab-nexus"}
docker compose logs --tail=50 nexus
```

Primera entrada:

1. Abrir `http://192.168.1.3:8888`.
2. Iniciar sesión con `NEXUS_USER` / `NEXUS_PASSWORD`.
3. Revisar que aparecen los 19 servicios y que los puntos de estado se van poblando.
4. Abrir un par de tarjetas y comprobar que las URLs corresponden al modo de red activo.

### 6.4 Actualización

El contenedor se construye localmente; no hay imagen en un registro.

```bash
cd /home/mrdavid/HomeLab/docker/nexus
docker compose build --pull
docker compose up -d
docker compose logs --tail=30 nexus
```

Los datos viven en `./data`, así que reconstruir la imagen no afecta a la bóveda ni a la bitácora.

---

## 7. Operación diaria

| Tarea | Comando |
|---|---|
| Estado del contenedor | `docker ps --filter name=homelab-nexus` |
| Reiniciar | `docker compose restart nexus` |
| Reconstruir y levantar | `docker compose up -d --build` |
| Detener | `docker compose stop nexus` |
| Logs en vivo | `docker compose logs -f --tail=200 nexus` |
| Health check manual | `curl -fsS http://127.0.0.1:8888/health` |
| Uso de recursos | `docker stats --no-stream homelab-nexus` |
| Shell dentro del contenedor | `docker exec -it homelab-nexus sh` |
| Inspeccionar SQLite | `docker exec -it homelab-nexus python -c "import sqlite3; c=sqlite3.connect('/app/data/nexus.sqlite3'); print(c.execute('select count(*) from vault_items').fetchone())"` |

Rutina recomendada:

- Revisar `docker compose logs --tail=100 nexus` tras cada actualización.
- Confirmar que el healthcheck no aparece como `unhealthy`: `docker inspect --format '{{.State.Health.Status}}' homelab-nexus`.
- Vigilar el crecimiento de `data/`; con 19 servicios y una bitácora moderada se mantiene en pocos MB.
- Rota la contraseña maestra y las credenciales de la bóveda si el dispositivo se pierde. Cambiar `NEXUS_PASSWORD` solo requiere reiniciar el contenedor; cambiar `NEXUS_MASTER_KEY` invalida los cifrados existentes. Tras rotar la contraseña, borrar la tabla `sessions` para expulsar cualquier sesión abierta (ver §8.4).

---

## 8. Respaldos y restauración

La base de datos está en modo WAL. Copiar el archivo a mano mientras el contenedor escribe puede producir una copia inconsistente: usar la API de backup de SQLite.

### 8.1 Respaldo en caliente (recomendado)

```bash
cd /home/mrdavid/HomeLab/docker/nexus
STAMP=$(date +%Y%m%d_%H%M)
DEST=/home/mrdavid/backups-compose/${STAMP}_nexus
mkdir -p "$DEST"

docker exec -i homelab-nexus python - <<'PY'
import sqlite3
src = sqlite3.connect('/app/data/nexus.sqlite3')
dst = sqlite3.connect('/app/data/nexus-backup.sqlite3')
src.backup(dst)
dst.close()
src.close()
print('backup ok')
PY

docker cp homelab-nexus:/app/data/nexus-backup.sqlite3 "$DEST/nexus.sqlite3"
docker exec homelab-nexus rm -f /app/data/nexus-backup.sqlite3
sha256sum "$DEST/nexus.sqlite3" | tee "$DEST/SHA256SUMS"
```

El resultado es un único archivo consistente que incluye servicios, contraseñas cifradas, runbooks, notas y sesiones activas.

### 8.2 Respaldo en frío

```bash
cd /home/mrdavid/HomeLab/docker/nexus
docker compose stop nexus
sudo tar -czf /home/mrdavid/backups-compose/nexus_$(date +%Y%m%d).tar.gz data/
docker compose start nexus
```

Al detener el contenedor, SQLite cierra y consolida el WAL; el `tar` es seguro. No incluir `.env` en el archivo comprimido.

### 8.3 Qué guardar y dónde

| Elemento | ¿Respaldo? | Dónde |
|---|---|---|
| `data/nexus.sqlite3` | Sí | `backups-compose/` + copia externa |
| `NEXUS_MASTER_KEY` | **Sí, crítico** | Gestor de contraseñas |
| `.env` completo | Sí | Gestor de contraseñas (nunca en Git) |
| Código (`app/`, `Dockerfile`, `compose`) | Sí | Repositorio Git |
| `data/` y `.env` | No versionar | Ignorados por `.gitignore` |

### 8.4 Restauración

```bash
cd /home/mrdavid/HomeLab/docker/nexus
docker compose stop nexus

# data/ pertenece a root porque el contenedor corre como root
sudo install -m 600 /ruta/al/respaldo/nexus.sqlite3 data/nexus.sqlite3
sudo rm -f data/nexus.sqlite3-wal data/nexus.sqlite3-shm

docker compose start nexus
curl -fsS http://127.0.0.1:8888/health
```

Comprobaciones posteriores:

1. Iniciar sesión con las credenciales del `.env` actual.
2. Verificar que aparecen los 19 servicios y la bitácora.
3. Probar **Copiar Contraseña** en un servicio con credencial guardada.
4. Si el respaldo es antiguo, las sesiones restauradas pueden seguir siendo válidas hasta su fecha de expiración. Para invalidarlas:

```bash
docker exec -i homelab-nexus python - <<'PY'
import sqlite3
c = sqlite3.connect('/app/data/nexus.sqlite3')
c.execute('DELETE FROM sessions')
c.commit()
print('sesiones invalidadas')
PY
```

> Si `NEXUS_MASTER_KEY` no coincide con la usada al crear el respaldo, las contraseñas se descifran como cadena vacía. La base de datos sigue siendo válida, pero los secretos se pierden.

---

## 9. Seguridad

### 9.1 Modelo de protección

- **Acceso:** usuario y contraseña maestra comparados con `secrets.compare_digest` (tiempo constante).
- **Sesión:** token aleatorio de 48 bytes (`secrets.token_urlsafe`), guardado en SQLite y entregado en cookie `nexus_session` con `HttpOnly` y `SameSite=Lax`.
- **Cifrado:** Fernet (AES-128-CBC + HMAC-SHA256) con clave derivada de `NEXUS_MASTER_KEY`.
- **Persistencia:** `data/` y `.env` fuera del repositorio; la base de datos no se sirve por HTTP.
- **Service worker:** nunca cachea respuestas de `/api/`; solo guarda el shell estático. Los secretos no quedan en la caché del navegador.

### 9.2 Límites conocidos

- La cookie se emite con `secure=False` para permitir HTTP en la LAN. Por eso **no debe exponerse el puerto 8888 a Internet**.
- No hay WebAuthn ni segundo factor propio; la biometría es la del sistema operativo al autocompletar la credencial.
- No hay límite de intentos de login en la aplicación. Si se publica fuera de la LAN, colocar un proxy con rate limiting y CrowdSec delante.
- Los health checks y los runbooks HTTP usan `verify=False`: aceptan certificados autofirmados. Es intencionado para Portainer y servicios internos.
- Un runbook HTTP puede invocar cualquier URL accesible desde el contenedor. Solo un usuario autenticado puede dispararlo; tratar la cuenta como privilegiada.

### 9.3 Recomendaciones

- [ ] `chmod 600 .env` y `chmod 700 data`.
- [ ] Contraseña maestra larga y única; guardada en el Llavero/gestor.
- [ ] `NEXUS_MASTER_KEY` respaldada por separado; nunca en Git, capturas o chat.
- [ ] Sin reenvío de puertos en el router. Acceso remoto solo por Tailscale.
- [ ] HTTPS para la PWA completa (service worker + limpieza del portapapeles).
- [ ] Respaldo probado al menos una vez (restaurar en un entorno de prueba).
- [ ] Revisar `docker compose logs` tras cada cambio.

### 9.4 HTTPS dentro de la tailnet (opcional)

Para obtener contexto seguro sin abrir nada a Internet, se puede publicar Nexus solo en la tailnet con Tailscale Serve en un puerto distinto del que ya usa qBittorrent:

```bash
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8888
sudo tailscale serve status
```

La app quedaría en `https://server.tailbd32c4.ts.net:8443`. Antes de aplicarlo, comprobar que el mapeo actual del puerto 443 sigue intacto y que `tailscale funnel status` indica que Funnel está desactivado. No se debe cambiar el `serve` existente de qBittorrent.

---

## 10. Referencia de la API

Todas las rutas `/api/*` (salvo `/api/login`) requieren la cookie de sesión. Las respuestas de error usan `{"detail": "..."}`.

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | Estado del servicio (sin auth) |
| `POST` | `/api/login` | Inicia sesión y emite cookie |
| `POST` | `/api/logout` | Cierra sesión y borra el token |
| `GET` | `/api/me` | Usuario actual |
| `GET` | `/api/items` | Lista de servicios (sin contraseñas) |
| `POST` | `/api/items` | Crea un servicio |
| `PUT` | `/api/items/{id}` | Actualiza un servicio |
| `DELETE` | `/api/items/{id}` | Elimina un servicio |
| `POST` | `/api/items/{id}/reveal` | Devuelve la contraseña descifrada |
| `GET` | `/api/status-all` | Comprueba todos los health checks |
| `GET` | `/api/corp-office-state` | Estado vivo de los 9 personajes (Corp + Sentinel) y enlaces activos |
| `POST` | `/api/corp-office-command` | `{"action":"director_round"}` o `{"action":"assign_review","employee":"qa"}`; solo webhooks predefinidos |
| `GET` | `/api/runbooks` | Lista de runbooks |
| `POST` | `/api/runbooks/{id}/run` | Ejecuta un runbook HTTP o devuelve el comando |
| `GET` | `/api/notes` | Lista de notas |
| `POST` | `/api/notes` | Crea una nota |
| `PUT` | `/api/notes/{id}` | Actualiza una nota |
| `DELETE` | `/api/notes/{id}` | Elimina una nota |

Rutas estáticas: `/`, `/manifest.json`, `/sw.js`, `/icon-192.png`, `/icon-512.png`, `/static/*`.

---

## 11. Solución de problemas

| Síntoma | Causa probable | Solución |
|---|---|---|
| El contenedor reinicia en bucle | Falta `.env` o `NEXUS_MASTER_KEY` en el primer arranque | Revisar `docker compose logs nexus`; completar `.env` |
| `401 No autenticado` tras el login | Cookie bloqueada, origen distinto o sesión expirada | Abrir siempre el mismo origen; borrar cookies del sitio; volver a entrar |
| Todos los servicios en rojo | Sin ruta a la LAN, Nodo 2 apagado o health checks mal definidos | Probar `curl` desde el contenedor; revisar la URL de health |
| El Nodo 2 aparece offline | El equipo está apagado | Es normal; encenderlo con WoL desde el bot o el panel |
| «No se pudo copiar» | Contexto no seguro o permiso de portapapeles | Servir por HTTPS; permitir portapapeles en el navegador |
| La contraseña copiada no se borra a los 30 s | Se abrió por HTTP (sin Clipboard API) | Usar HTTPS o limpiar el portapapeles manualmente |
| La PWA no se instala en iPhone | Se está usando un navegador que no es Safari | Abrir en Safari y usar **Compartir → Añadir a pantalla de inicio** |
| Chrome no muestra «Instalar página como aplicación» | Versión antigua o política corporativa | Usar **Crear acceso directo → Abrir como ventana** |
| Los iconos no aparecen | Falta `icon-192.png` o `icon-512.png` | Restaurar los archivos en `app/static/` y reconstruir |
| El puerto 8888 está ocupado | Otro servicio en el host | `ss -ltnp \| grep 8888` y liberar o remapear |
| Las contraseñas salen vacías | `NEXUS_MASTER_KEY` cambió | Restaurar la clave original desde el gestor |
| La base parece bloqueada | WAL sin checkpoint o copia manual | Reiniciar el contenedor; respaldar con la API de SQLite |
| Oficina IA sin datos / «Ledger Corp SIN CONEXIÓN» | Falta `CORP_INTERNAL_TOKEN` o el Nodo 2 está apagado | Copiar la variable desde `docker/ai-corp/.env`, reiniciar Nexus y comprobar `curl http://192.168.1.4:8006/health` |
| Las burbujas muestran «sin dato» | Ledger vacío o Sentinel en caché fría | Ejecutar una ronda del Director y volver a abrir la pestaña |

---

## 12. Mapa de archivos

```
docker/nexus/
├── docker-compose.yml     # servicio, puerto 8888, límites, healthcheck
├── Dockerfile             # python:3.11-slim + uvicorn
├── requirements.txt       # FastAPI, httpx, cryptography
├── .env                   # NO versionado (credenciales y clave)
├── data/                  # NO versionado
│   ├── nexus.sqlite3
│   ├── nexus.sqlite3-wal
│   └── nexus.sqlite3-shm
└── app/
    ├── main.py            # rutas, middleware de auth y estáticos
    ├── auth.py            # login, sesiones y cookie HttpOnly
    ├── crypto.py          # Fernet derivado de NEXUS_MASTER_KEY
    ├── db.py              # esquema SQLite, WAL y helper de conexión
    ├── seed.py            # 19 servicios, 11 runbooks y nota inicial
    ├── status.py          # sondas HTTP/TCP en paralelo
    ├── corp_office.py     # puente Corp + Sentinel, estados y comandos permitidos
    └── static/
        ├── index.html     # shell de la PWA (pestaña Oficina IA)
        ├── styles.css     # tema oscuro glassmorphism
        ├── office.css     # escenario, actores, burbujas y ficha
        ├── app.js         # lógica de UI, red, bóveda y runbooks
        ├── office.js      # SVG animado, polling 4 s y fichas táctiles
        ├── sw.js          # service worker (shell cache v3)
        ├── manifest.json  # metadatos de instalación
        ├── icon-192.png
        └── icon-512.png
```

---

**HomeLab Nexus** · Centro de mando del HomeLab · `:8888` · Documentación mantenida junto al código en `docker/nexus/README.md`.
