# Two-Node Hybrid HomeLab Infrastructure

A self-hosted platform that separates always-on services from on-demand compute. Node 1 runs Debian 12 and 20 containers for Nextcloud, Immich, Jellyfin, Pi-hole and active defense on a Dell OptiPlex 5040; Node 2 stays off until Wake-on-LAN brings it up for Immich machine learning and cold backups. Remote access goes through a Tailscale tailnet, with no inbound ports exposed to the internet.

## System Architecture

```text
                        iOS / macOS / Android TV
                         |                    |
            LAN 192.168.1.0/24      Tailscale 100.64.0.0/10
                         |                    |
                         v                    v
+----------------------------------+   +----------------------------------+
| Node 1: server                   |   | Node 2: homelab-node2            |
| Dell OptiPlex 5040               |   | AMD FX-8300, 8 cores             |
| 192.168.1.3 / 100.98.35.64       |   | 192.168.1.4 / 100.67.30.11       |
| always on                        |   | on demand, Wake-on-LAN           |
|                                  |   |                                  |
| Pi-hole             :53, :80     |   | immich-machine-learning   :3003  |
| Unbound      127.0.0.1:5335       |   | portainer_agent           :9001  |
| CrowdSec LAPI 127.0.0.1:8085     |   |                                  |
|   bouncer iptables (host)        |   |                                  |
| Nextcloud           :8080        |   |                                  |
| Immich              :2283        |   |                                  |
| Jellyfin            :8096 (host) |   | /mnt/storage (1 TB HDD, ext4)    |
| qBittorrent     :8081, :6881     |   |   immich-ml-cache/               |
| Radarr / Sonarr  :7878 / :8989   |   |   backups/immich/                |
| Prowlarr / Bazarr :9696 / :6767  |   |                                  |
| FlareSolverr        :8191        |   | 223.6 GB Kingston SSD: OS only   |
| Threadfin           :34400       |   |                                  |
| Portainer        :9443, :8000    |   |                                  |
| Sentinel AI         :9090        |   |                                  |
|                                  |   |                                  |
| seerr-theme         :5055        |   |                                  |
|   nginx -> jellyseerr:5055       |   |                                  |
|   loopback 127.0.0.1:5056        |   |                                  |
|                                  |   |                                  |
| volumes:                         |   |                                  |
|   nextcloud_nextcloud_data       |   |                                  |
|     -> Immich external :ro       |   |                                  |
|   media/backups -> NFS Node 2     |   |                                  |
|     -> /media, hardlinked        |   |                                  |
+----------------+-----------------+   +----------------+-----------------+
                 |                                      ^
                 | SSH, rsync, Immich ML                | Wake-on-LAN
                 +--------------------------------------+ magic packet
```

LAN clients connect to the static addresses on Node 1. Tailscale clients use the 100.x addresses from anywhere. `tailscale serve` publishes qBittorrent at `https://server.tailbd32c4.ts.net` to the tailnet only; Funnel is disabled. Node 2 has no route from the internet and no published service that Node 1 depends on for core operation.

## Hardware & Workload Placement

| | Node 1: server | Node 2: homelab-node2 |
|---|---|---|
| Role | Always-on control plane, private cloud, media and DNS | On-demand machine learning and cold storage |
| Hardware | Dell OptiPlex 5040 SFF | Custom AMD desktop |
| CPU | Intel Core i5-6500, 4C/4T, 3.2 GHz, Skylake | AMD FX-8300, 8 cores |
| Memory | 8 GB (7.6 GiB usable), 1 GB swap | 3.8 GiB |
| Graphics | Intel HD 530, QuickSync on `/dev/dri/renderD128` | Radeon RX 550 4 GB, Mesa 22.3.6 VA-API |
| Storage | 228 GB SSD, ext4; 56 GB free at audit | 223.6 GB Kingston SSD for the OS, 1 TB Toshiba HDD for data (916 GB usable, 868 GB free) |
| Network | 1 GbE, `192.168.1.3/24` | 1 GbE, `192.168.1.4/24` |
| OS | Debian 12, kernel 6.1.0-53-amd64 | Debian 12, kernel 6.1.0-56-amd64, Docker 29.10.0, Compose 5.8.0 |
| Power | 24/7 | On demand, `Wake-on: g` on `enp3s0` |
| Workloads | Nextcloud, Immich server, PostgreSQL and Valkey, Pi-hole, Unbound, CrowdSec, Jellyfin, the Servarr stack, Threadfin, Seerr, Portainer, ChatOps bot, Sentinel AI | `immich-machine-learning`, `portainer_agent`, ML model cache and the backup target |

Node 1 has 7.6 GiB usable and currently uses 4.4 GiB across 20 containers. Immich server alone reached 1.55 GiB during indexing. A 2.5 GiB machine-learning worker on the same host would leave under 1 GiB of headroom and force page-cache reclaim under indexing load. Node 2 has 3.8 GiB and a GPU that exposes VA-API, so it takes the ML container and the backup disk. The split also keeps DNS, Nextcloud and Jellyfin available when Node 2 is off.

Node 2 is a capacity and storage tier, not a standby cluster. There is no automatic failover for Pi-hole, Nextcloud or the media stack. A Node 1 disk failure is a restore-from-backup event, not a cluster election.

## Core Engineering Highlights

### Zero-Trust Networking & DNS Sinkholing

The ZTE F6201B at `192.168.1.1` is the only DHCP and NAT device. The Nexxt Amp300 at `192.168.1.254` is a bridge on 2.4 GHz, channel 6, 20 MHz, so it adds no second DHCP scope. Both servers set static addresses in `/etc/network/interfaces`. The ZTE ignored the `.3` and `.4` reservations, and the DHCP pool starts at `.5`, so a lease cannot collide with either node.

Pi-hole v6 answers DNS on `192.168.1.3:53` over TCP and UDP and serves its admin UI on `:80`. It forwards to exactly one upstream: `unbound#5335` on the private `pihole_default` Docker network. No Google, Cloudflare or ISP resolver is configured. Unbound (`klutchell/unbound:latest`, 128 MB limit) resolves from the root servers, validates DNSSEC with an RFC 5011 trust anchor (`/var/unbound/root.key`), enables `qname-minimisation`, prefetch and serve-expired, and only accepts queries from `127.0.0.0/8`, `192.168.1.0/24`, `172.16.0.0/12` and `100.64.0.0/10`. Its host port is published only on `127.0.0.1:5335` for diagnostics, so LAN and tailnet clients still go through Pi-hole. The StevenBlack list blocks about 77,000 domains and returns `0.0.0.0` for sinkholed names. IPv4 filtering is complete. The ZTE router advertisement still hands out `fe80::1` as IPv6 DNS and the UI has no field to change it, so clients that bypass Tailscale can escape Pi-hole over IPv6. That is a known gap, not a hidden one.

DNSSEC is validated end to end, not just advertised: `sigfail.verteiltesysteme.net` returns `SERVFAIL` (invalid signature rejected) and `sigok.verteiltesysteme.net` returns `NOERROR` with the `AD` flag. Sentinel checks both every 30 s and can restart Unbound automatically if resolution stops.

### Active Defense — CrowdSec IDS/IPS

CrowdSec runs from `docker/security/` with the `crowdsecurity/linux`, `crowdsecurity/sshd` and `crowdsecurity/nginx` collections. It reads host SSH events from `/var/log/auth.log` (rsyslog forwards journald, because Debian 12 does not create `auth.log` by default) and its Local API is published only on `127.0.0.1:8085`. The host runs `crowdsec-firewall-bouncer-iptables` as a systemd service, with jumps in `INPUT` and `DOCKER-USER`, so bans cover both host services and published containers. Docker rules and Tailscale chains are left untouched.

The whitelist is explicit and loaded as a local parser (`parsers/s02-enrich/mywhitelists.yaml`): `127.0.0.1`, `192.168.1.0/24`, `100.64.0.0/10` (the whole Tailscale mesh, including Node 2) and `172.16.0.0/12` (Docker networks). A failed SSH login from a LAN address is parsed, matched by the private whitelist and ignored before it can create a decision; Node 2 and SSH access are never at risk. Sentinel surfaces the active decision count, recent alerts, whitelist hits, and the bouncer's last pull in Mission Control.

Tailscale links Node 1, Node 2, an iPhone and a MacBook. Funnel is disabled. `tailscale serve --bg http://127.0.0.1:8081` publishes qBittorrent at `https://server.tailbd32c4.ts.net` to the tailnet only. No router port is forwarded. SSH, Jellyfin, Nextcloud, Immich and Portainer stay on LAN or tailnet addresses.

### Zero-Duplication Storage & Distributed ML (Immich + Nextcloud)

Nextcloud runs from the `nextcloud_nextcloud_data` volume, about 119 GB. The user tree holds 30,142 files (99.7 GiB), and the preview cache adds 14,028 files (10 GiB), for 44,170 files in total. Immich's library tracks 20,600 media assets (65.6 GiB), most of them from the photo tree. Photos, video and thumbnails come to roughly 76 GiB. The `Photos` directory is bind-mounted read-only into Immich at `/usr/src/app/external/nextcloud-photos:ro` and registered as an external library. The originals never enter Immich's upload volume, so the SSD does not hold a second copy.

Immich server has a 2 GB and 2 CPU limit. PostgreSQL runs the vectorchord and pgvectors image with 768 MB and one CPU; Valkey gets 256 MB and half a CPU. `immich-config.json` caps thumbnail and video jobs at one worker, face and smart-search jobs at one, and metadata extraction at two. That keeps the 4-core host responsive during a first scan.

Machine learning runs on Node 2. `immich-machine-learning` gets 2560 MB and 6 CPUs, caches models in `/mnt/storage/immich-ml-cache` (1.7 GB at audit), and answers on `:3003`. The Immich server points at `http://192.168.1.4:3003`. When Node 2 is off, browsing, uploads and the API stay up; only ML jobs wait. That failure mode is deliberate.

### Hardware-Accelerated Media Pipeline & Language Scoring

qBittorrent, Radarr, Sonarr, Bazarr and Jellyfin bind `/home/mrdavid/HomeLab/media` at `/media` (symlinked to Node 2 NFS offload `/mnt/node2-storage/media`, same filesystem view for hardlinks). Downloads land in `/media/descargas`; Radarr and Sonarr create atomic hardlinks in `/media/peliculas` and `/media/series`. The inode is shared, so seeding continues while Jellyfin reads the library and the SSD does not store two copies. This only works because every container sees one filesystem, not separate bind mounts per app.

Radarr and Sonarr expose three quality profiles (IDs 7, 8 and 9). The default for Seerr is `1080p - Español Latino / Dual`, with `minFormatScore` set to `+500`. Custom Formats award Latino `+1000`, Dual `+700`, Castellano `+800` and Spanish subtitles `+100`. A non-Spanish dual or multi release loses 2500 points; an English-only release loses 2000. A release that cannot reach `+500` is rejected. The rule is blunt on purpose: a missing Spanish release stays monitored instead of silently downloading an English file.

Jellyfin runs with host networking and `/dev/dri`, with `HardwareAccelerationType=qsv` on `/dev/dri/renderD128`. Skylake QuickSync handles H.264 encode and decode, HEVC 8-bit decode, MPEG-2 and VC-1. HEVC encode and 10-bit decode are disabled because the iGPU does not support them; leaving them on would cause silent software fallbacks. Library metadata uses `es-419` for Dominican Spanish.

Threadfin runs on `:34400` with a 512 MB and 1.5 CPU limit and serves the Android TV in the living room, separate from Jellyfin. The lineup is 426 channels numbered 2-647 in eight groups (211 Dominican, 215 Spanish-language international), delivered as direct provider URLs so the server is not in the playback path. A root cron job runs `threadfin_sync.py` at 04:30: it probes every stream with `curl` plus `ffprobe` inside the container, swaps dead, geo-blocked or slow links for the fastest healthy alternative of the same `tvg-id` while preserving channel numbers, groups and logos, and rebuilds a pruned XMLTV guide from EPGShare01 country feeds (57 matched channels, 2.7 MB, against the 95 MB Spain-only DobleM guide that matched 5 of 405 channels). A per-minute guard restores the curated M3U and XML if Threadfin regenerates them empty on restart. The ffmpeg stream buffer is set to 3 MB with a 1500 ms timeout in RAM, and the player user-agent is `VLC/3.0.20 LibVLC/3.0.20`; playlists, EPG dumps and logs stay in `conf/`, outside Git.

### Non-Invasive Reverse Proxy UI Customization

Seerr's official image is not patched. A 32 MB `nginx:alpine` container named `seerr-theme` listens on `:5055` and proxies to `jellyseerr:5055` over the Docker network. Seerr itself publishes only `127.0.0.1:5056` on the host. Nginx clears `Accept-Encoding`, sets `sub_filter_once off`, and injects an 11.5 KB stylesheet before `</head>`. A `map` handles websocket upgrades; `X-Forwarded-*` headers preserve host, port and scheme.

Steady-state RSS is 5.5 to 5.6 MiB, well under the 32 MB cap. The container peaked at 14.8 MiB during startup. `docker compose pull` replaces Seerr without touching the theme. The CSS lives in `docker/media-stack/seerr-theme/html` and is mounted as a directory, so editing it and reloading Nginx is enough.

`5055` is the only Seerr entry point on the LAN. `5056` is loopback for diagnostics. Mapping `5055` on the Seerr container would make the proxy fail to bind, so that mapping stays out of the compose file.

### HomeLab Sentinel AI — Digital Twin & Cognitive Auto-Healing

`homelab-sentinel` runs on Node 1 as a host-network, privileged Python 3.11 service (FastAPI + APScheduler) exposed at `:9090` (`http://192.168.1.3:9090`). It keeps a live digital twin of the homelab and performs deterministic monitoring with optional LLM-assisted diagnosis.

- **Digital Twin (`twin_engine.py`):** auto-discovers Docker containers (state, health, networks, mounts and CPU/RAM stats), host telemetry (CPU temperature, RAM, `/dev/sda1` with early alert at 92%, Tailscale interface and QuickSync `/dev/dri/renderD128`) and physical/network nodes: ZTE gateway `192.168.1.1`, Nexxt AP `192.168.1.254`, OptiPlex host `192.168.1.3`, **`homelab-node2` `192.168.1.4` (activo 24/7, crítico, conectado a la LAN/AP Nexxt; AMD FX-8300, 4 GB RAM, RX 550, SSD 240 GB + HDD 1 TB `/mnt/storage`, BIOS auto-encendido)** and TCL Android TV 12 `192.168.1.8`. Detects/saves Node 2 MAC (ARP/ChatOps config) and keeps Wake-on-LAN as a manual recovery action in the node card. Time series, incidents and repair history are stored in SQLite (`docker/sentinel/data/sentinel.db`).
- **Deep L7 Probes (`probes.py`, every 30 s):** Threadfin (`/m3u/threadfin.m3u`, `/xmltv/threadfin.xml`, `docker/threadfin/logs/last_run.json` for active-channel/sync health), Pi-hole real DNS resolution and filtering latency, **Unbound DNSSEC** (`127.0.0.1:5335`: normal resolution, `sigfail` must return `SERVFAIL`, `sigok` must return `NOERROR`+`AD`), **CrowdSec IPS** (`cscli metrics`, active `decisions`, recent `alerts`, bouncer last pull and the private whitelist), Nextcloud `/status.php`, Immich `/api/server/ping` plus `/nextcloud-photos:ro` mount verification, Jellyfin `/System/Info/Public`, disk margin on `/dev/sda1`, container/log scans for `unhealthy`/`restarting` states and patterns (`OOM`, `FATAL`, `I/O error`, `connection refused`) with `optiplex-chatops` transient Telegram polling noise filtered (`[WARN]` `EFATAL`/`ETIMEDOUT`/`ECONNRESET`/`ENOTFOUND` to `api.telegram.org`), **Node 2 probes** (Portainer Agent `192.168.1.4:9001` TCP/TLS, Immich ML `http://192.168.1.4:3003/ping`, SSH metrics with RAM early alert >85% of 4 GB and `/mnt/storage` free space), and **NFS offload probe** validating `/mnt/node2-storage` (Node 1 ← Node 2) is mounted/accessible, alerting if the link drops.
- **Cognitive Brain & Guarded Auto-Healing (`ai_brain.py`, `healer.py`):** a deterministic detector runs every 30 s with 0 LLM tokens in normal state. When anomalies are found it escalates to `SENTINEL_LLM_PROVIDER` (`gemini|openrouter|openai`, `SENTINEL_API_KEY`) with digital-twin context and the last 60 log lines of the affected service (Gemini: `X-goog-api-key`, `gemini-flash-latest` primary, `gemini-flash-lite-latest` fallback), falling back to an offline rule engine when no key/internet/provider response is available. **Tier 1 (automatic, max 2 actions / 30 min / service):** restart hung/unhealthy containers, restart Unbound when DNS stops resolving, restart CrowdSec when its LAPI/engine is down, run `threadfin_sync.sh --publish` when Threadfin M3U/EPG is empty, clean safe temporary caches if `/dev/sda1` exceeds 95%, and restart remote Immich ML on Node 2 (SSH, via nsenter/host key) when ML is down while `192.168.1.4` is online. **Tier 2 (one-tap approval):** sends an ntfy notification (`NTFY_TOPIC`) in Spanish with diagnosis and signed HTTP Approve/Ignore actions (30 min cooldown per service/anomaly type while pending or ignored), including critical Node 2-down/NFS-link alerts (WoL available as manual recovery/action).
- **Mission Control (`http://192.168.1.3:9090`):** dark-mode dashboard with interactive digital-twin map (ZTE → Nexxt → OptiPlex/Node 2/TCL TV → Containers), `/dev/sda1` meter, live auto-repair/incident feed, “Auditar Todo con IA Ahora”, and a console for questions about state/logs. Manual audits run asynchronously (`asyncio.to_thread` for heavy probes/collection) so `/api/state` polling stays responsive (no UI freeze/`Failed to fetch` during audit), the audit modal renders an executive report (general state of both nodes, summary of 11 main L7 probes, anomalies and Gemini diagnosis) with the full technical payload in a collapsible `<details>` JSON block, and console replies render safe Markdown (headings, bold, inline/fenced code, lists).

Runtime config: `docker/sentinel/.env` (see `.env.example`). State/logs DB lives in `docker/sentinel/data/` (ignored by Git).

### NFS Offload to Node 2 (Nodo 1 SSD Relief)

To relieve pressure on the OptiPlex SSD, Node 1 offloads heavy non-database data to Node 2’s 1 TB HDD over NFSv4. `homelab-node2` (`192.168.1.4`) runs `nfs-kernel-server` and exports `/mnt/storage/homelab-offload` **only** to `192.168.1.3` (`rw,sync,no_subtree_check,no_root_squash`), with subdirectories `backups-compose/` and `media/` owned by `1000:1000`. Node 1 mounts it at `/mnt/node2-storage` via `/etc/fstab` using resilient options: `192.168.1.4:/mnt/storage/homelab-offload /mnt/node2-storage nfs4 _netdev,nofail,soft,timeo=30,retrans=3 0 0` (so boot does not block if Node 2 is restarting).

`/home/mrdavid/backups-compose` and `/home/mrdavid/HomeLab/media` are symlinked to `/mnt/node2-storage/backups-compose` and `/mnt/node2-storage/media` respectively (migrated with `rsync -aHAX --checksum` and integrity verification; media consumers `jellyfin`, `qbittorrent`, `radarr`, `sonarr`, `bazarr` were stopped only during the media sync/cutover). This keeps the media pipeline hardlink-capable within the same mounted filesystem while moving bulk storage off `/dev/sda1`; availability of those paths depends on Node 2 and the NFS link (`soft` mount). Sentinel exposes and monitors `/mnt/node2-storage` (read-only mount in container) as an L7 probe and raises an alert if the NFS link is down. Docker build cache pruning (`docker builder prune -f`) is used for safe local cleanup without removing active images/volumes.

After migration/cutover and cleanup (local `media.bak_verify` removed, `docker builder prune -f`), Node 1 `/dev/sda1` usage dropped to ~85% (~33 GB free) with Sentinel probes (including NFS) reporting OK. Jellyfin required a container recreate after an overlay/rootfs startup error during the media cutover and was verified serving with all 5 media consumers accessing `/media` on the NFS symlink.

### Fault-Tolerant Cross-Node Backup Automation

`scripts/backup_immich_to_node2.sh` runs from cron at 03:30. It opens SSH with `BatchMode=yes`, `ConnectTimeout=8` and the dedicated `node2_bot` key. If Node 2 is off, the probe fails and the script exits 0, leaving no failed cron mail and no partial dump. If Node 2 answers, it runs `pg_dump` on `immich-postgres`, gzips the dump (26 MB at audit), keeps seven days locally, and rsyncs the Immich upload library plus the dumps to `/mnt/storage/backups/immich/{library,db}`.

The local Immich library held about 7 GB across 2,300 files during the audit, so the script uses `rsync -a` for an incremental copy instead of a full transfer every night. The first end-to-end run on 2026-11-18 mirrored 7.4 GB and a 26 MB database dump to the HDD. The database dump is transactionally consistent; the file copy is incremental and may pick up an upload that is still being written. The next run corrects it. Node 2 mounts the 1 TB Toshiba HDD by UUID with `nofail`, and the Kingston SSD holds only the OS. The backup target is the HDD.

Wake-on-LAN is enabled with `Wake-on: g` on `enp3s0`. The ChatOps bot sends a magic packet to the broadcast address before a maintenance window, and the same node can be powered off after the copy finishes.

### Unified Multi-Node Container Management

Portainer CE on Node 1 binds the local Docker socket and serves its UI on `:9443` (HTTPS). The compose file also publishes `:8000` for edge agents. Node 2 runs `portainer_agent` on `:9001`. One Portainer UI manages both endpoints, including the on-demand ML container.

Compose files are the IaC layer: one directory per stack, one `docker-compose.yml` per service group, and no manual `docker run` in normal operation. Portainer is the operating console, not a second configuration store.

Node 2 has no Swarm and no permanent application stack. Its only containers are the Portainer agent and, when the node is awake, Immich ML. `docker compose stop` on Node 2 releases the 2.5 GB ML reservation without touching Node 1.

## Repository Layout & Deployment Standards

```text
HomeLab/
├── docker/
│   ├── immich/                   Immich server, PostgreSQL and Valkey
│   │   ├── docker-compose.yml
│   │   ├── immich-config.json
│   │   ├── .env.example
│   │   └── .gitignore
│   ├── immich-ml/                Node 2 machine-learning worker
│   │   ├── docker-compose.yml
│   │   ├── .env.example
│   │   └── README.md
│   ├── jellyfin/                 Host-network Jellyfin with QuickSync
│   │   └── docker-compose.yml
│   ├── media-stack/              Servarr, qBittorrent, Seerr and the Nginx theme proxy
│   │   ├── docker-compose.yml
│   │   ├── .env.example
│   │   └── seerr-theme/
│   │       ├── conf/default.conf
│   │       └── html/custom-theme.css
│   ├── nextcloud/                Nextcloud and its external volume
│   │   └── docker-compose.yml
│   ├── pihole/                   DNS sinkhole + recursive Unbound
│   │   ├── docker-compose.yml
│   │   ├── unbound/homelab.conf
│   │   └── .env.example
│   ├── portainer/                Docker management UI
│   │   └── docker-compose.yml
│   ├── security/                 CrowdSec IDS/IPS, whitelist and bouncer
│   │   ├── docker-compose.yml
│   │   ├── .gitignore
│   │   └── crowdsec/
│   │       ├── parsers/s02-enrich/mywhitelists.yaml
│   │       └── acquis.d/homelab.yaml
│   ├── sentinel/                 Digital twin, L7 probes and AI auto-healing
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── twin_engine.py
│   │   │   ├── probes.py
│   │   │   ├── ai_brain.py
│   │   │   ├── healer.py
│   │   │   ├── db.py
│   │   │   ├── templates/
│   │   │   └── static/
│   │   ├── data/                 SQLite state (ignored)
│   │   ├── Dockerfile
│   │   ├── docker-compose.yml
│   │   ├── requirements.txt
│   │   ├── .env                  Runtime secrets (ignored)
│   │   └── .env.example
│   ├── telegram-bot/             ChatOps agent
│   │   ├── bot.js
│   │   ├── config.js
│   │   ├── Dockerfile
│   │   ├── package.json
│   │   ├── lib/
│   │   ├── modules/
│   │   ├── docker-compose.yml
│   │   └── .env.example
│   └── threadfin/                Live TV proxy and curation script
│       ├── docker-compose.yml
│       ├── .env.example
│       ├── apply_curated.sh
│       └── README.md
├── scripts/
│   └── backup_immich_to_node2.sh
├── media/                        Host media path, excluded from Git
├── docs/
├── .gitignore
├── GEMINI.md                     Operational notes for the local agent
└── README.md
```

Deployment rules:

- Every change to a compose file or shared configuration starts with a timestamped copy under `/home/mrdavid/backups-compose/`. Rollback is a file copy, not a rebuild.
- Memory and CPU limits are explicit on containers that can grow: Immich 2 GB and 2 CPUs, PostgreSQL 768 MB and 1 CPU, Valkey 256 MB and 0.5 CPU, Immich ML 2560 MB and 6 CPUs, Unbound 128 MB, CrowdSec 256 MB, Threadfin 512 MB and 1.5 CPUs, Nginx 32 MB. Jellyfin and the bot run without hard caps because they need host devices or host networking.
- Secrets live in `.env` files excluded by Git. `.env.example` files contain placeholders only. No database, config directory, media file, model cache, playlist, EPG dump or log is tracked.
- Scripts use `set -euo pipefail`, log through `logger` and stdout, and return a non-zero status only when an operation that should have completed did not.
- Node 2 uses UUID mounts and never writes to the Kingston system SSD. The Toshiba HDD is the only data disk.
- Remote access has no inbound port forwards. Tailscale Funnel is disabled; Serve is tailnet-only.
- A history scan for `.env` files, private keys and common token formats returned no tracked secret. Threadfin playlists and EPG data stay in `conf/`, outside Git.

A quick health check from the administration host:

```bash
ssh homelab 'docker ps --format "table {{.Names}}\t{{.Status}}"'
ssh homelab 'dig +short @127.0.0.1 github.com && dig @127.0.0.1 -p 5335 sigfail.verteiltesysteme.net A +dnssec | grep status'
ssh homelab 'docker exec crowdsec cscli decisions list && docker exec crowdsec cscli bouncers list'
ssh homelab 'sudo tailscale serve status'
ssh homelab 'ssh -i ~/.ssh/node2_bot mrdavid@192.168.1.4 "findmnt /mnt/storage && df -h /mnt/storage"'
```


## Full-Cluster Tuning & End-to-End Audit (2026-11-20)

Pre-change snapshot: `/home/mrdavid/backups-compose/20261120_full_cluster_tune_pre/` (11 compose files, sysctl, DNS/security configs, Git state). Repo synced with `git pull --rebase` first.

| Area | Parameter | Node 1 | Node 2 | Verification |
| --- | --- | --- | --- | --- |
| Memory | `vm.swappiness` | 60 → **10** | 60 → **15** | `sysctl --system` applied |
| Memory | `vm.vfs_cache_pressure` | 100 → **50** | 100 → **50** | applied |
| FS watch | `fs.inotify.max_user_watches` | 60745 → **524288** | — | applied (Nextcloud/Immich/Jellyfin) |
| FS watch | `fs.inotify.max_user_instances` | 128 → **1024** | — | applied |
| Network | `net.core.rmem_max` / `wmem_max` | 212992 → **16777216** | 212992 → **16777216** | applied |
| Network | `tcp_rmem` / `tcp_wmem` max | 6 MB / 4 MB → **16 MB** | → **16 MB** | applied |
| Network | `net.core.somaxconn` | 4096 → **4096** | 128 → **4096** | applied |
| SSD | `fstrim.timer` | enabled + active | enabled + active | `systemctl is-*` |
| NFS | `RPCNFSDCOUNT` | — | **8** (already correct) | `/etc/default/nfs-kernel-server` |
| Docker logs | `json-file` `max-size=10m`, `max-file=3` | 8 stacks (21 services) | immich-ml | `docker inspect .HostConfig.LogConfig` |
| DNS | Unbound `prefetch`, `prefetch-key`, `serve-expired` | all **yes** | — | `unbound-checkconf` OK |
| DNS | Pi-hole `FTLCONF_database_maxDBdays` | 91 → **14** | — | `pihole-FTL --config` = 14 |
| Nextcloud | Missing DB indices / cron mode | none / `cron` | — | `occ db:add-missing-indices` (no-op, exit 0) |
| Immich | `immich-server` healthcheck `start_period` | **300s** | — | avoids false `unhealthy` during `vchordrq_prewarm` |
| Sentinel | Transient log false-positive filters | Postgres startup FATAL; seerr-theme 502 with later 200 | — | `probes.py`, image rebuilt |

Config files added: `/etc/sysctl.d/99-homelab-tuning.conf` (N1), `/etc/sysctl.d/99-homelab-node2-tuning.conf` (N2).

**Findings corrected:** Docker logs 57 MB → **3 MB** (largest were `nextcloud` 34 MB and `immich-server` 14 MB); build cache reclaimed **420.7 MB**; transient `immich-postgres` recovery-mode FATAL and `seerr-theme` 502s traced to restarts and resolved; Bazarr `subf2m`/`addic7ed` throttling (missing User-Agent/credentials) and empty `/media/peliculas` logged as informational, not changed (no data touched). `portainer` remains an unmanaged, externally-created container (no Compose labels): its 1.7 MB log has no rotation until a future managed recreate; the empty `portainer_default` network created by the failed `up` was removed.

**Verified final state:** Sentinel `POST /api/audit` → `ok:true`, **13/13 probes OK, 0 anomalies** (`threadfin`, `pihole`, `unbound_dnssec`, `crowdsec_ips`, `nextcloud`, `immich`, `jellyfin`, `node2_portainer`, `node2_immich_ml`, `node2_metrics`, `containers_logs`, `disk`, `nfs_node2_storage`). DNSSEC `sigfail`→`SERVFAIL` / `sigok`→`NOERROR`; CrowdSec bouncer active with 0 decisions and the private-subnet whitelist intact; `/dev/sda1` 86% (32 GB free, below the 92% alert); Nodo 2 RAM 24.4%; no OOMKilled and no restart loops; all official healthchecks healthy.
