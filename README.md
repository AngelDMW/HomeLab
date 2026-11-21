# Dual-Node Hybrid HomeLab Infrastructure

A self-hosted platform that separates always-on infrastructure from a 24/7 AI compute and storage tier. Node 1 runs Debian 12 and 22 containers for DNS, private cloud, photos, media, security and operations on a Dell OptiPlex 5040. Node 2 runs the HomeLab Corp AI organization, Immich machine learning and the NFSv4 cold-storage export on an AMD FX-8300. Remote access goes through a Tailscale tailnet, with no inbound ports exposed to the internet. Sentinel AI watches 15 L7 probes, Nexus provides the personal command center, and the Telegram bot exposes the whole cluster through natural language.

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
| always on                        |   | 24/7 AI Corp + ML + cold storage |
|                                  |   |                                  |
| Pi-hole             :53, :80     |   | homelab-corp-n8n          :5678  |
| Unbound      127.0.0.1:5335       |   | corp-tools-api            :8006  |
| CrowdSec LAPI 127.0.0.1:8085     |   | immich-machine-learning   :3003  |
|   bouncer iptables (host)        |   | portainer_agent           :9001  |
| Nextcloud           :8080        |   |                                  |
|   nextcloud-db (PostgreSQL 16)   |   | /mnt/storage (1 TB HDD, ext4)    |
| Immich              :2283        |   |   homelab-offload/               |
| Jellyfin            :8096 (host) |   |     ai-corp/{n8n,ledger}/        |
| qBittorrent     :8081, :6881     |   |     backups-criticos/            |
| Radarr / Sonarr  :7878 / :8989   |   |     media/                       |
| Prowlarr / Bazarr :9696 / :6767  |   |                                  |
| FlareSolverr        :8191        |   | 223.6 GB Kingston SSD: OS only   |
| Threadfin           :34400       |   |                                  |
| Seerr               :5055        |   |                                  |
| Portainer        :9443, :8000    |   |                                  |
| Sentinel AI         :9090        |   |                                  |
| Nexus               :8888        |   |                                  |
| Telegram bot     host network    |   |                                  |
|                                  |   |                                  |
| volumes:                         |   |                                  |
|   nextcloud_nextcloud_data       |   |                                  |
|     -> Immich external :ro       |   |                                  |
|   media/backups -> NFS Node 2     |   |                                  |
|     -> /media, hardlinked        |   |                                  |
+----------------+-----------------+   +----------------+-----------------+
                 |                                      ^
                 | NFSv4, SSH, rsync, Immich ML         | Wake-on-LAN
                 +--------------------------------------+ magic packet
```

LAN clients connect to the static addresses on Node 1. Tailscale clients use the 100.x addresses from anywhere. Node 2 is now always on because HomeLab Corp runs there 24/7; Wake-on-LAN remains the recovery path after a power loss. `tailscale serve --bg http://127.0.0.1:8081` publishes qBittorrent at `https://server.tailbd32c4.ts.net` to the tailnet only; Funnel is disabled. Node 2 has no inbound route from the internet. Node 1 does depend on it for the NFS media/backup mount and for HomeLab Corp, so the mount uses `nofail` and the node keeps Wake-on-LAN plus BIOS auto-power-on as recovery paths.

## Hardware & Workload Placement

| | Node 1: server | Node 2: homelab-node2 |
|---|---|---|
| Role | Always-on control plane, private cloud, media, DNS and operations | 24/7 AI Corp, machine learning and cold storage |
| Hardware | Dell OptiPlex 5040 SFF | Custom AMD desktop |
| CPU | Intel Core i5-6500, 4C/4T, 3.2 GHz, Skylake | AMD FX-8300, 8 cores |
| Memory | 8 GB (7.6 GiB usable), 1 GB swap | 3.8 GiB |
| Graphics | Intel HD 530, QuickSync on `/dev/dri/renderD128` | Radeon RX 550 4 GB, Mesa 22.3.6 VA-API |
| Storage | 228 GB SSD, ext4; 31 GB free at last audit | 223.6 GB Kingston SSD for the OS, 1 TB Toshiba HDD for data (916 GB usable, 808 GB free) |
| Network | 1 GbE, `192.168.1.3/24` | 1 GbE, `192.168.1.4/24` |
| OS | Debian 12, kernel 6.1.0-53-amd64 | Debian 12, kernel 6.1.0-56-amd64, Docker 29.10.0, Compose 5.8.0 |
| Power | 24/7 | 24/7 for Corp; `Wake-on: g` on `enp3s0` as recovery |
| Workloads | Nextcloud and PostgreSQL, Immich server/PostgreSQL/Valkey, Pi-hole, Unbound, CrowdSec, Jellyfin, the Servarr stack, Threadfin, Seerr, Portainer, ChatOps bot, Sentinel AI, Nexus | `homelab-corp-n8n`, `corp-tools-api`, `immich-machine-learning`, `portainer_agent`, NFS export and backup target |

Node 1 has 7.6 GiB usable and runs 22 containers at roughly 4.2 GiB. Immich server alone reached 1.55 GiB during indexing. Node 2 has 3.8 GiB and uses about 1.5 GiB across the Corp stack and ML worker; its 1 TB HDD is at 8% (808 GB free). The split keeps DNS, Nextcloud and Jellyfin available on Node 1 while the AI organization runs on the node with the GPU and the bulk disk.

Node 2 is a capacity, AI and storage tier, not a standby cluster. There is no automatic failover for Pi-hole, Nextcloud or the media stack. A Node 1 disk failure is a restore-from-backup event, not a cluster election. Corp depends on Node 2, so the node is treated as critical and covered by Wake-on-LAN, BIOS auto-power-on and the NFS `nofail` mount.

## Core Engineering Highlights

### Zero-Trust Networking & DNS Sinkholing

The ZTE F6201B at `192.168.1.1` is the only DHCP and NAT device. The Nexxt Amp300 at `192.168.1.254` is a bridge on 2.4 GHz, channel 6, 20 MHz, so it adds no second DHCP scope. Both servers set static addresses in `/etc/network/interfaces`. The ZTE ignored the `.3` and `.4` reservations, and the DHCP pool starts at `.5`, so a lease cannot collide with either node.

Pi-hole v6 answers DNS on `192.168.1.3:53` over TCP and UDP and serves its admin UI on `:80`. It forwards to exactly one upstream: `unbound#5335` on the private `pihole_default` Docker network. No Google, Cloudflare or ISP resolver is configured. Unbound (`klutchell/unbound:latest`, 128 MB limit) resolves from the root servers, validates DNSSEC with an RFC 5011 trust anchor (`/var/unbound/root.key`), enables `qname-minimisation`, prefetch and serve-expired, and only accepts queries from `127.0.0.0/8`, `192.168.1.0/24`, `172.16.0.0/12` and `100.64.0.0/10`. Its host port is published only on `127.0.0.1:5335` for diagnostics, so LAN and tailnet clients still go through Pi-hole. The StevenBlack list blocks about 77,000 domains and returns `0.0.0.0` for sinkholed names.

DNSSEC is validated end to end, not just advertised: `sigfail.verteiltesysteme.net` returns `SERVFAIL` (invalid signature rejected) and `sigok.verteiltesysteme.net` returns `NOERROR` with the `AD` flag. Sentinel checks both every 30 s and can restart Unbound automatically if resolution stops. IPv4 filtering is complete; the ZTE router advertisement still hands out `fe80::1` as IPv6 DNS and the UI has no field to change it, so clients that bypass Tailscale can escape Pi-hole over IPv6. That is a known gap, not a hidden one.

### Active Defense — CrowdSec IDS/IPS

CrowdSec runs from `docker/security/` with the `crowdsecurity/linux`, `crowdsecurity/sshd` and `crowdsecurity/nginx` collections. It reads host SSH events from `/var/log/auth.log` (rsyslog forwards journald, because Debian 12 does not create `auth.log` by default) and its Local API is published only on `127.0.0.1:8085`. The host runs `crowdsec-firewall-bouncer-iptables` as a systemd service, with jumps in `INPUT` and `DOCKER-USER`, so bans cover both host services and published containers. Docker rules and Tailscale chains are left untouched.

The whitelist is explicit and loaded as a local parser (`parsers/s02-enrich/mywhitelists.yaml`): `127.0.0.1`, `192.168.1.0/24`, `100.64.0.0/10` (the whole Tailscale mesh, including Node 2) and `172.16.0.0/12` (Docker networks). A failed SSH login from a LAN address is parsed, matched by the private whitelist and ignored before it can create a decision; Node 2 and SSH access are never at risk. Sentinel surfaces the active decision count, recent alerts, whitelist hits and the bouncer's last pull in Mission Control.

Tailscale links Node 1, Node 2, an iPhone and a MacBook. Funnel is disabled. `tailscale serve --bg http://127.0.0.1:8081` publishes qBittorrent at `https://server.tailbd32c4.ts.net` to the tailnet only. No router port is forwarded. SSH, Jellyfin, Nextcloud, Immich, Nexus and Portainer stay on LAN or tailnet addresses.

### Zero-Duplication Storage & Distributed ML (Immich + Nextcloud)

Nextcloud runs from the `nextcloud_nextcloud_data` volume, about 119 GB. The user tree holds about 30,100 files (~100 GiB), and the preview cache adds about 14,000 files (~10 GiB), for roughly 44,000 files in total. Since 2026-11-21 the metadata lives in PostgreSQL 16.16 (`nextcloud-db`, 384 MB limit, `nextcloud_nextcloud_db_data`), with the original 88 MB SQLite database kept at `data/owncloud.db` as a rollback copy. Immich's library tracks about 20,600 media assets (~66 GB), most of them from the photo tree; asset files plus generated thumbnails come to roughly 76 GiB. The `Photos` directory is bind-mounted read-only into Immich at `/usr/src/app/external/nextcloud-photos:ro` and registered as an external library. The originals never enter Immich's upload volume, so the SSD does not hold a second copy.

Immich server has a 2 GB and 2 CPU limit. PostgreSQL runs the vectorchord and pgvectors image with 768 MB and one CPU; Valkey gets 256 MB and half a CPU. `immich-config.json` caps thumbnail and video jobs at one worker, face and smart-search jobs at one, and metadata extraction at two. That keeps the 4-core host responsive during a first scan.

Machine learning runs on Node 2. `immich-machine-learning` gets 2560 MB and 6 CPUs, caches models in `/mnt/storage/immich-ml-cache`, and answers on `:3003`. The Immich server points at `http://192.168.1.4:3003`. The ML container can be stopped independently without affecting browsing, uploads or the API; only ML jobs wait. That failure mode is deliberate.

### Hardware-Accelerated Media Pipeline & Language Scoring

qBittorrent, Radarr, Sonarr, Bazarr and Jellyfin bind `/home/mrdavid/HomeLab/media` at `/media` (symlinked to the Node 2 NFS offload `/mnt/node2-storage/media`, same filesystem view for hardlinks). Downloads land in `/media/descargas`; Radarr and Sonarr create atomic hardlinks in `/media/peliculas` and `/media/series`. The inode is shared, so seeding continues while Jellyfin reads the library and the SSD does not store two copies. This only works because every container sees one filesystem, not separate bind mounts per app.

Radarr and Sonarr expose three quality profiles (IDs 7, 8 and 9). The default for Seerr is `1080p - Español Latino / Dual`, with `minFormatScore` set to `+500`. Custom Formats award Latino `+1000`, Dual `+700`, Castellano `+800` and Spanish subtitles `+100`. A non-Spanish dual or multi release loses 2500 points; an English-only release loses 2000. A release that cannot reach `+500` is rejected. The rule is blunt on purpose: a missing Spanish release stays monitored instead of silently downloading an English file.

Jellyfin runs with host networking and `/dev/dri`, with `HardwareAccelerationType=qsv` on `/dev/dri/renderD128`. Skylake QuickSync handles H.264 encode and decode, HEVC 8-bit decode, MPEG-2 and VC-1. HEVC encode and 10-bit decode are disabled because the iGPU does not support them; leaving them on would cause silent software fallbacks. Library metadata uses `es-419` for Dominican Spanish.

Threadfin runs on `:34400` with a 512 MB and 1.5 CPU limit and serves the Android TV in the living room, separate from Jellyfin. The lineup is 426 channels numbered 2-647 in eight groups (211 Dominican, 215 Spanish-language international), delivered as direct provider URLs so the server is not in the playback path. A root cron job runs `threadfin_sync.py` at 04:30: it probes every stream with `curl` plus `ffprobe` inside the container, swaps dead, geo-blocked or slow links for the fastest healthy alternative of the same `tvg-id` while preserving channel numbers, groups and logos, and rebuilds a pruned XMLTV guide from EPGShare01 country feeds (57 matched channels, 2.7 MB). A per-minute guard restores the curated M3U and XML if Threadfin regenerates them empty on restart.

### Non-Invasive Reverse Proxy UI Customization

Seerr's official image is not patched. A 64 MB `nginx:alpine` container named `seerr-theme` listens on `:5055` and proxies to `jellyseerr:5055` over the Docker network. Seerr itself publishes only `127.0.0.1:5056` on the host. Nginx clears `Accept-Encoding`, sets `sub_filter_once off`, and injects a 17 KB stylesheet before `</head>`. A `map` handles websocket upgrades; `X-Forwarded-*` headers preserve host, port and scheme. The container idles around 7 MiB RSS, well under the 64 MB cap, and `docker compose pull` replaces Seerr without touching the theme.

`5055` is the only Seerr entry point on the LAN. `5056` is loopback for diagnostics. Mapping `5055` on the Seerr container would make the proxy fail to bind, so that mapping stays out of the compose file.

### HomeLab Sentinel AI — Digital Twin & Mission Control

`homelab-sentinel` runs on Node 1 as a host-network, privileged Python 3.11 service (FastAPI + APScheduler) exposed at `:9090`. It keeps a live digital twin of both nodes and performs deterministic monitoring with optional LLM-assisted diagnosis.

- **Digital twin:** auto-discovers Docker containers (state, health, networks, mounts and CPU/RAM stats), host telemetry (CPU temperature, RAM, `/dev/sda1` with early alert at 92%, Tailscale and QuickSync) and the physical nodes: ZTE gateway, Nexxt AP, OptiPlex host, Node 2 and the TCL Android TV. Time series, incidents and repair history live in SQLite (`docker/sentinel/data/sentinel.db`).
- **15 L7 probes every 30 s:** Threadfin, Pi-hole DNS, Unbound DNSSEC, CrowdSec IPS, Nextcloud, Immich, Jellyfin, **Nexus**, Node 2 Portainer Agent, Node 2 Immich ML, **Node 2 AI Corp**, Node 2 SSH metrics, container/log scans, disk margin and the NFS offload mount. The audit summary reports all 15 with zero anomalies in the current baseline.
- **Cognitive brain and guarded auto-healing:** a deterministic detector runs every 30 s with zero LLM tokens in normal state. When anomalies appear it escalates to Gemini/OpenRouter/OpenAI with the digital-twin context and the last 60 log lines, falling back to an offline rule engine without a key or internet. Tier 1 executes at most two safe actions per service every 30 minutes (restart hung containers, reload Unbound or CrowdSec, republish Threadfin, clean safe caches, restart remote Immich ML). Tier 2 sends an ntfy notification with signed Approve/Ignore actions and a 30-minute cooldown.
- **Mission Control (`http://192.168.1.3:9090`):** interactive twin map, `/dev/sda1` meter, live repair/incident feed, asynchronous “Auditar Todo con IA Ahora” and a console that renders safe Markdown. Manual audits run in a worker thread so `/api/state` stays responsive.

### NFSv4 Shared Storage & Offload

To relieve pressure on the OptiPlex SSD, Node 1 offloads heavy non-database data to Node 2's 1 TB HDD over NFSv4. `homelab-node2` runs `nfs-kernel-server` and exports `/mnt/storage/homelab-offload` **only** to `192.168.1.3` (`rw,sync,no_subtree_check,no_root_squash`), with subdirectories `backups-compose/`, `media/`, `ai-corp/` and `backups-criticos/` owned by `1000:1000`. Node 1 mounts it at `/mnt/node2-storage` via `/etc/fstab` with resilient options: `192.168.1.4:/mnt/storage/homelab-offload /mnt/node2-storage nfs4 _netdev,nofail,soft,timeo=30,retrans=3 0 0`.

`/home/mrdavid/backups-compose` and `/home/mrdavid/HomeLab/media` are symlinked to `/mnt/node2-storage/backups-compose` and `/mnt/node2-storage/media` respectively (migrated with `rsync -aHAX --checksum` and integrity verification; media consumers were stopped only during the cutover). The mount uses NFSv4.2 with 8 server threads over 1 GbE. `soft` keeps Node 1 from hanging if Node 2 reboots, and `nofail` keeps boot independent of the NFS link. Sentinel monitors `/mnt/node2-storage` read-only and alerts if the link drops.

### Fault-Tolerant Cross-Node Backup Automation

`scripts/backup_immich_to_node2.sh` runs from cron at 03:30. It opens SSH with `BatchMode=yes`, `ConnectTimeout=8` and the dedicated `node2_bot` key. If Node 2 is unreachable, the script exits 0 without a partial dump. If Node 2 answers, it runs `pg_dump` on `immich-postgres`, gzips the dump, keeps seven days locally, and rsyncs the Immich upload library plus the dumps to `/mnt/storage/backups/immich/{library,db}`. The database dump is transactionally consistent; the file copy is incremental and the next run corrects any upload caught mid-write.

HomeLab Corp adds a verified 3-2-1 configuration backup via `scripts/backup_corp_criticos.sh`: `pg_dump -Fc` plus `pg_restore -l` validation for Immich, Nextcloud `config.php`, consistent SQLite backups of Sentinel and Nexus with `PRAGMA integrity_check`, a JSON manifest with sizes and missing-file checks, 7-day retention, and a copy to `/mnt/node2-storage/backups-criticos/`. It never copies `/media`, the Nextcloud data directory or the Immich library. The latest run (`20261121_175856`) reports `missing_or_empty: []` with six artifacts and nine n8n workflows.

### Unified Multi-Node Container Management

Portainer CE on Node 1 binds the local Docker socket and serves its UI on `:9443` (HTTPS). The compose file also publishes `:8000` for edge agents. Node 2 runs `portainer_agent` on `:9001`. One Portainer UI manages both endpoints, including the Corp stack and the ML container.

Compose files are the IaC layer: one directory per stack, one `docker-compose.yml` per service group, and no manual `docker run` in normal operation. Portainer is the operating console, not a second configuration store. Node 2 has no Swarm; its HomeLab-managed stack is Corp, Immich ML and the agent, and `docker compose stop` on the ML service releases the 2.5 GB reservation without touching Node 1.

## Repository Layout & Deployment Standards

```text
HomeLab/
├── docker/
│   ├── ai-corp/                  HomeLab Corp: n8n + corp-tools-api + 9 workflows
│   │   ├── docker-compose.yml
│   │   ├── corp-tools-api/
│   │   │   ├── app.py
│   │   │   ├── autonomy.py
│   │   │   └── Dockerfile
│   │   ├── workflows/            00_director + 8 employees
│   │   └── .env.example
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
│   ├── nextcloud/                Nextcloud + PostgreSQL 16
│   │   ├── docker-compose.yml
│   │   ├── Dockerfile            nextcloud:latest + ffmpeg
│   │   └── .env.example          NEXTCLOUD_DB_PASSWORD
│   ├── nexus/                    Personal command center and encrypted vault
│   │   ├── app/
│   │   ├── data/                 SQLite state (ignored)
│   │   ├── Dockerfile
│   │   ├── docker-compose.yml
│   │   ├── requirements.txt
│   │   └── README.md             Dedicated operations guide
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
│   ├── sentinel/                 Digital twin, 15 L7 probes and AI auto-healing
│   │   ├── app/
│   │   ├── data/                 SQLite state (ignored)
│   │   ├── Dockerfile
│   │   ├── docker-compose.yml
│   │   ├── requirements.txt
│   │   ├── .env                  Runtime secrets (ignored)
│   │   └── .env.example
│   ├── telegram-bot/             ChatOps agent v3.0 + Python AI brain
│   │   ├── bot.js
│   │   ├── ai_brain/
│   │   ├── lib/
│   │   ├── modules/
│   │   ├── Dockerfile
│   │   ├── docker-compose.yml
│   │   └── .env.example
│   └── threadfin/                Live TV proxy and curation script
│       ├── docker-compose.yml
│       ├── .env.example
│       ├── apply_curated.sh
│       └── README.md
├── scripts/
│   ├── backup_corp_criticos.sh   3-2-1 configuration backup
│   └── backup_immich_to_node2.sh Nightly Immich database and library copy
├── media/                        Host media path, excluded from Git
├── docs/
├── .gitignore
├── GEMINI.md                     Operational notes for the local agent
└── README.md
```

Deployment rules:

- Every change to a compose file or shared configuration starts with a timestamped copy under `/home/mrdavid/backups-compose/`. Rollback is a file copy, not a rebuild.
- Memory and CPU limits are explicit on containers that can grow: Immich 2 GB and 2 CPUs, PostgreSQL 768 MB and 1 CPU, Valkey 256 MB and 0.5 CPU, Immich ML 2560 MB and 6 CPUs, Unbound 128 MB, CrowdSec 256 MB, Threadfin 512 MB and 1.5 CPUs, Nginx 64 MB, n8n 1024 MB and 1 CPU, `corp-tools-api` 256 MB and 0.35 CPU, Nexus 192 MB. Jellyfin and the bot run without hard caps because they need host devices or host networking.
- Secrets live in `.env` files excluded by Git. `.env.example` files contain placeholders only. No database, config directory, media file, model cache, playlist, EPG dump or log is tracked.
- Scripts use `set -euo pipefail`, log through `logger` and stdout, and return a non-zero status only when an operation that should have completed did not.
- Node 2 uses UUID mounts and never writes to the Kingston system SSD. The Toshiba HDD is the only data disk.
- Remote access has no inbound port forwards. Tailscale Funnel is disabled; Serve is tailnet-only.
- Corp endpoints require `X-Corp-Token`; tokens live only in `.env` files and are never printed, committed or pasted into chat.
- A history scan for `.env` files, private keys and common token formats returned no tracked secret. Threadfin playlists and EPG data stay in `conf/`, outside Git.

A quick health check from the administration host:

```bash
ssh homelab 'docker ps --format "table {{.Names}}\t{{.Status}}"'
ssh homelab 'curl -fsS http://127.0.0.1:8888/health && curl -fsS http://127.0.0.1:9090/health'
ssh homelab 'curl -fsS http://192.168.1.4:8006/health && curl -fsS http://192.168.1.4:5678/healthz'
ssh homelab 'curl -sS -X POST http://127.0.0.1:9090/api/audit'
ssh homelab 'dig @127.0.0.1 -p 5335 sigfail.verteiltesysteme.net A +dnssec | grep status'
ssh homelab 'docker exec crowdsec cscli decisions list && docker exec crowdsec cscli bouncers list'
ssh homelab 'sudo tailscale serve status'
```

## HomeLab Nexus — Personal Command Center

`homelab-nexus` is the single entry point for day-to-day operations at `:8888` (`http://192.168.1.3:8888` on LAN, `http://100.98.35.64:8888` over Tailscale). It is a FastAPI + SQLite PWA with a 192 MB limit, a `GET /health` check every 30 s and a dedicated operations guide in `docker/nexus/README.md`.

| Capability | Implementation |
|---|---|
| Unified launcher | 19 seeded services across five categories, favorites, filters, CRUD and per-service health checks |
| Smart network switch | Each item stores a LAN URL and a Tailscale URL; the toggle repaints every card and persists in `localStorage` |
| Encrypted vault | Fernet-encrypted passwords, plaintext revealed only through an authenticated session, 30-second clipboard wipe when served over HTTPS or localhost (plain-HTTP LAN falls back to copy without auto-clear) |
| Live status | All 19 health targets checked in parallel every 60 s (HTTP < 500 or TCP connect), with manual refresh |
| Search and actions | Global search on `Cmd/Ctrl + K`, 11 runbooks (HTTP triggers and copy-ready SSH/log/diagnostic commands) and a persistent technical log |
| Native app | PWA manifest and service worker; installable from Safari on iPhone and Chrome on macOS, with Face ID/Touch ID autofill through iCloud Keychain |

The vault key is derived from `NEXUS_MASTER_KEY` and is independent from the login password. Losing that key makes stored passwords unrecoverable, so it is backed up in the password manager, never in Git. The full deployment, backup and restore procedure is documented in `docker/nexus/README.md`.

## HomeLab Corp — Autonomous AI Organization (24/7)

HomeLab Corp is an eight-employee AI organization on Node 2 that reports to `@ServerDMW_bot` and to the Director. It is diagnostic and operational by design: employees inspect the cluster, file evidence and propose actions, while every destructive or state-changing operation requires a guardrail or an explicit Telegram approval.

| Component | Location | Role |
|---|---|---|
| `homelab-corp-n8n` | Node 2 `:5678`, 1 GB / 1 CPU | 9 workflows: Director plus 8 employees; cron at 08:00 and 20:00 |
| `corp-tools-api` | Node 2 `:8006`, 256 MB / 0.35 CPU | Read-only department tools, accountability ledger and autonomy endpoints |
| `HomeLab-Director` | n8n webhook `/webhook/director` | Opens a task, calls employees in parallel, routes deliveries to QA, requests the executive report and closes the task |
| Ledger | `/mnt/storage/homelab-offload/ai-corp/ledger/corp_ledger.sqlite3` | `tasks`, `employee_reports`, `interventions`, `updates`, `backups`, `improvement_proposals` (WAL, `integrity_check=ok`) |
| Sentinel link | `node2_ai_corp` probe | Checks `:8006/health` and `:5678/healthz` every 30 s |

| Employee | Focus |
|---|---|
| `Agent-Infra` | Docker, NFS, host capacity and service health |
| `Agent-SecOps` | Pi-hole, Unbound DNSSEC, CrowdSec and external exposure |
| `Agent-Media` | qBittorrent, Radarr, Sonarr, Bazarr, Jellyfin and Threadfin |
| `Agent-Cloud` | Nextcloud, Immich, storage and backups |
| `Agent-Updater` | Image digests, healthchecks and rollback |
| `Agent-Backup` | 3-2-1 verification, manifests and retention |
| `Agent-Architect` | Improvement proposals and risk analysis |
| `Agent-QA` | Audits Sentinel and scores every delivery |

Autonomy and safety:

- `POST /api/tools/autofix` routes safe interventions through Sentinel (`restart_container`, `reload_unbound`, `reload_crowdsec`, `inspect_service`, `clean_build_cache`), capped at two actions per service every 30 minutes.
- `POST /api/tools/updater` records the image digest, runs the healthcheck and rolls back automatically on failure; history is exposed at `/api/tools/updater/history`.
- `POST /api/tools/backup` runs the verified 3-2-1 configuration backup; history is exposed at `/api/tools/backup/history`. The latest run (`20261121_175856`) is complete, with six artifacts, nine workflows and no missing files.
- `POST /api/tools/architect` files improvement proposals in the ledger. `POST /api/ledger/proposal/{id}/apply` only runs after explicit approval from Telegram.
- `X-Corp-Token` protects every tool, ledger and autonomy endpoint; Sentinel's `/api/corp/action` uses a separate shared token. Tokens live only in `.env` files.
- The platform never deletes `/media`, Nextcloud data or Immich libraries. The backup explicitly excludes them.

Operations:

```bash
ssh homelab-node2 "cd /home/mrdavid/HomeLab-node2/ai-corp && docker compose up -d --build"
curl -fsS http://192.168.1.4:8006/health
curl -fsS http://192.168.1.4:5678/healthz
curl -X POST http://192.168.1.4:5678/webhook/director \
  -H 'Content-Type: application/json' \
  -d '{"directive":"Ejecuta una ronda de inspección y entrega el reporte ejecutivo"}'
```

## Telegram Bot 3.0 — AI ChatOps

`optiplex-chatops` v3.0.0 is an AI-native ChatOps agent. The Node.js front end keeps telemetry, self-healing, torrent/storage guardians and Node 2 control, and delegates conversation to a Python brain in the same container on `127.0.0.1:5057` under `start.sh`. Total memory is about 76 MB.

| Capability | Implementation |
|---|---|
| Conversational engine | Gemini 2.5 Flash with automatic OpenRouter fallback, plus an offline rule engine |
| Function calling | 8 real tools covering cluster status, Sentinel audits, security, media, container management and Corp |
| Memory | SQLite `/app/data/ai_memory.sqlite` (WAL), last 12 turns per chat; `/clear` wipes it |
| Voice notes | Telegram audio downloaded in memory, transcribed and executed in the same step; no temporary files on disk |
| Strict access | `ALLOWED_CHAT_IDS` from `CHAT_ID`; messages and callbacks require matching chat and sender; foreign chats get HTTP 403 |
| Touch menu | `/menu` with two-column inline keyboard; restart actions always ask for confirmation |
| Corp integration | `/corp` shows the ledger; `/corp <directive>` sends a directive to the Director; `/corp propuestas` lists improvement proposals |
| Sentinel bridge | Polls `/api/state` every 5 minutes and forwards new anomalies with diagnosis and restart buttons |

Commands: `/menu`, `/status`, `/audit`, `/security`, `/media`, `/clear`, `/corp`, plus the original `/mission`, `/telemetry`, `/containers`, `/queue`, `/buscar`, `/briefing`, `/iptv`, `/disk`, `/maint`, `/node2`, `/selftest` and `/help`. The selftest reports 12/12 integrations OK.

## Services & Ports

| Node | Service | Port(s) | Exposure | Role |
|---|---|---|---|---|
| 1 | Pi-hole | `53/tcp+udp`, `80/tcp` | LAN + tailnet | DNS sinkhole and admin UI |
| 1 | Unbound | `127.0.0.1:5335` | Loopback | Recursive DNSSEC resolver |
| 1 | CrowdSec LAPI | `127.0.0.1:8085` | Loopback | IDS/IPS local API |
| 1 | Nextcloud | `8080/tcp` | LAN + tailnet | Private cloud and files |
| 1 | `nextcloud-db` | `5432/tcp` | Docker internal | PostgreSQL 16.16 |
| 1 | Immich | `2283/tcp` | LAN + tailnet | Photos and video |
| 1 | `immich-postgres` / `immich-redis` | `5432` / `6379` | Docker internal | Vector DB and Valkey |
| 1 | Jellyfin | `8096/tcp` | Host network | Media server with QuickSync |
| 1 | qBittorrent | `8081`, `6881` | LAN + tailnet | Torrent client |
| 1 | Radarr / Sonarr | `7878` / `8989` | LAN + tailnet | Movie and TV automation |
| 1 | Prowlarr / Bazarr | `9696` / `6767` | LAN + tailnet | Indexers and subtitles |
| 1 | FlareSolverr | `8191/tcp` | LAN + tailnet | Cloudflare bypass for indexers |
| 1 | Threadfin | `34400/tcp` | LAN + tailnet | Live TV M3U/XMLTV proxy |
| 1 | Seerr | `5055/tcp` (proxy), `127.0.0.1:5056` | LAN + tailnet | Media requests and HomeFlix UI |
| 1 | Portainer | `9443/tcp`, `8000/tcp` | LAN + tailnet | Docker management |
| 1 | Sentinel AI | `9090/tcp` | LAN + tailnet | Digital twin and Mission Control |
| 1 | Nexus | `8888/tcp` | LAN + tailnet | Personal command center |
| 1 | Telegram bot | Host network | Outbound only | ChatOps and alerts |
| 2 | `homelab-corp-n8n` | `5678/tcp` | LAN + tailnet | 9 Corp workflows |
| 2 | `corp-tools-api` | `8006/tcp` | LAN + tailnet | Tools, ledger and autonomy |
| 2 | Immich ML | `3003/tcp` | LAN + tailnet | Machine learning worker |
| 2 | Portainer agent | `9001/tcp` | LAN + tailnet | Node 2 Docker endpoint |

qBittorrent's WebUI is also published to the tailnet over HTTPS at `https://server.tailbd32c4.ts.net`; no other service is exposed through Tailscale Serve.

## Operations & Current Baseline

| Area | Setting | Node 1 | Node 2 |
|---|---|---|---|
| Memory | `vm.swappiness` | **10** | **15** |
| Memory | `vm.vfs_cache_pressure` | **50** | **50** |
| FS watch | `fs.inotify.max_user_watches` / `max_user_instances` | **524288 / 1024** | — |
| Network | `rmem_max` / `wmem_max` | **16 MB** | **16 MB** |
| Network | `net.core.somaxconn` | **4096** | **4096** |
| SSD | `fstrim.timer` | enabled + active | enabled + active |
| NFS | Server threads / client mount | `soft,timeo=30,retrans=3` | **8 threads** |
| Docker logs | `json-file` rotation | `10m`/`3` (`pihole` 10m/2, `unbound` 5m/2) | `10m`/`3` |
| DNS | Pi-hole `maxDBdays` | **14** | — |
| Nextcloud | Maintenance window | **03:00** | — |
| Sentinel | L7 probes | **15** | — |

Current verified baseline (2026-11-21): Sentinel audit `ok:true` with **15/15 probes and 0 anomalies**; Nexus healthy on `:8888` with 19 services, 11 runbooks and 1 note; Corp with 9/9 workflows active and the `20261121_175856` backup verified; bot selftest 12/12. Node 1 runs 22 containers, uses about 4.2 GiB of 7.6 GiB and has 31 GB free on `/dev/sda1` (86%). Node 2 runs the Corp stack, Immich ML and the Portainer agent at about 1.5 GiB of 3.8 GiB, with 808 GB free on `/mnt/storage`. DNSSEC returns `SERVFAIL` for `sigfail` and `NOERROR` + `AD` for `sigok`; CrowdSec has the private whitelist loaded and zero active decisions; no OOMKilled containers or restart loops are present.
