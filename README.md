# Two-Node Hybrid HomeLab Infrastructure

A self-hosted platform that separates always-on services from on-demand compute. Node 1 runs Debian 12 and 17 containers for Nextcloud, Immich, Jellyfin and Pi-hole on a Dell OptiPlex 5040; Node 2 stays off until Wake-on-LAN brings it up for Immich machine learning and cold backups. Remote access goes through a Tailscale tailnet, with no inbound ports exposed to the internet.

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
| Nextcloud           :8080        |   | portainer_agent           :9001  |
| Immich              :2283        |   |                                  |
| Jellyfin            :8096 (host) |   | /mnt/storage (1 TB HDD, ext4)    |
| qBittorrent     :8081, :6881     |   |   immich-ml-cache/               |
| Radarr / Sonarr  :7878 / :8989   |   |   backups/immich/                |
| Prowlarr / Bazarr :9696 / :6767  |   |                                  |
| FlareSolverr        :8191        |   | 223.6 GB Kingston SSD: OS only   |
| Threadfin           :34400       |   |                                  |
| Portainer        :9443, :8000    |   |                                  |
|                                  |   |                                  |
| seerr-theme         :5055        |   |                                  |
|   nginx -> jellyseerr:5055       |   |                                  |
|   loopback 127.0.0.1:5056        |   |                                  |
|                                  |   |                                  |
| volumes:                         |   |                                  |
|   nextcloud_nextcloud_data       |   |                                  |
|     -> Immich external :ro       |   |                                  |
|   /home/mrdavid/HomeLab/media    |   |                                  |
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
| Workloads | Nextcloud, Immich server, PostgreSQL and Valkey, Pi-hole, Jellyfin, the Servarr stack, Threadfin, Seerr, Portainer, ChatOps bot | `immich-machine-learning`, `portainer_agent`, ML model cache and the backup target |

Node 1 has 7.6 GiB usable and currently uses 4.4 GiB across 17 containers. Immich server alone reached 1.55 GiB during indexing. A 2.5 GiB machine-learning worker on the same host would leave under 1 GiB of headroom and force page-cache reclaim under indexing load. Node 2 has 3.8 GiB and a GPU that exposes VA-API, so it takes the ML container and the backup disk. The split also keeps DNS, Nextcloud and Jellyfin available when Node 2 is off.

Node 2 is a capacity and storage tier, not a standby cluster. There is no automatic failover for Pi-hole, Nextcloud or the media stack. A Node 1 disk failure is a restore-from-backup event, not a cluster election.

## Core Engineering Highlights

### Zero-Trust Networking & DNS Sinkholing

The ZTE F6201B at `192.168.1.1` is the only DHCP and NAT device. The Nexxt Amp300 at `192.168.1.254` is a bridge on 2.4 GHz, channel 6, 20 MHz, so it adds no second DHCP scope. Both servers set static addresses in `/etc/network/interfaces`. The ZTE ignored the `.3` and `.4` reservations, and the DHCP pool starts at `.5`, so a lease cannot collide with either node.

Pi-hole v6 answers DNS on `192.168.1.3:53` over TCP and UDP and serves its admin UI on `:80`. The ZTE advertises only Pi-hole to LAN clients, with `8.8.8.8` and `8.8.4.4` as upstreams. The StevenBlack list blocks about 77,000 domains and returns `0.0.0.0` for sinkholed names. IPv4 filtering is complete. The ZTE router advertisement still hands out `fe80::1` as IPv6 DNS and the UI has no field to change it, so clients that bypass Tailscale can escape Pi-hole over IPv6. That is a known gap, not a hidden one.

Tailscale links Node 1, Node 2, an iPhone and a MacBook. Funnel is disabled. `tailscale serve --bg http://127.0.0.1:8081` publishes qBittorrent at `https://server.tailbd32c4.ts.net` to the tailnet only. No router port is forwarded. SSH, Jellyfin, Nextcloud, Immich and Portainer stay on LAN or tailnet addresses.

### Zero-Duplication Storage & Distributed ML (Immich + Nextcloud)

Nextcloud runs from the `nextcloud_nextcloud_data` volume, about 119 GB. The user tree holds 30,142 files (99.7 GiB), and the preview cache adds 14,028 files (10 GiB), for 44,170 files in total. Immich's library tracks 20,600 media assets (65.6 GiB), most of them from the photo tree. Photos, video and thumbnails come to roughly 76 GiB. The `Photos` directory is bind-mounted read-only into Immich at `/usr/src/app/external/nextcloud-photos:ro` and registered as an external library. The originals never enter Immich's upload volume, so the SSD does not hold a second copy.

Immich server has a 2 GB and 2 CPU limit. PostgreSQL runs the vectorchord and pgvectors image with 768 MB and one CPU; Valkey gets 256 MB and half a CPU. `immich-config.json` caps thumbnail and video jobs at one worker, face and smart-search jobs at one, and metadata extraction at two. That keeps the 4-core host responsive during a first scan.

Machine learning runs on Node 2. `immich-machine-learning` gets 2560 MB and 6 CPUs, caches models in `/mnt/storage/immich-ml-cache` (1.7 GB at audit), and answers on `:3003`. The Immich server points at `http://192.168.1.4:3003`. When Node 2 is off, browsing, uploads and the API stay up; only ML jobs wait. That failure mode is deliberate.

### Hardware-Accelerated Media Pipeline & Language Scoring

qBittorrent, Radarr, Sonarr, Bazarr and Jellyfin bind the same host directory, `/home/mrdavid/HomeLab/media`, at `/media`. Downloads land in `/media/descargas`; Radarr and Sonarr create atomic hardlinks in `/media/peliculas` and `/media/series`. The inode is shared, so seeding continues while Jellyfin reads the library and the SSD does not store two copies. This only works because every container sees one filesystem, not separate bind mounts per app.

Radarr and Sonarr expose three quality profiles (IDs 7, 8 and 9). The default for Seerr is `1080p - Español Latino / Dual`, with `minFormatScore` set to `+500`. Custom Formats award Latino `+1000`, Dual `+700`, Castellano `+800` and Spanish subtitles `+100`. A non-Spanish dual or multi release loses 2500 points; an English-only release loses 2000. A release that cannot reach `+500` is rejected. The rule is blunt on purpose: a missing Spanish release stays monitored instead of silently downloading an English file.

Jellyfin runs with host networking and `/dev/dri`, with `HardwareAccelerationType=qsv` on `/dev/dri/renderD128`. Skylake QuickSync handles H.264 encode and decode, HEVC 8-bit decode, MPEG-2 and VC-1. HEVC encode and 10-bit decode are disabled because the iGPU does not support them; leaving them on would cause silent software fallbacks. Library metadata uses `es-419` for Dominican Spanish.

Threadfin runs on `:34400` with a 512 MB and 1.5 CPU limit and serves the Android TV in the living room, separate from Jellyfin. The lineup is 426 channels numbered 2-647 in eight groups (211 Dominican, 215 Spanish-language international), delivered as direct provider URLs so the server is not in the playback path. A root cron job runs `threadfin_sync.py` at 04:30: it probes every stream with `curl` plus `ffprobe` inside the container, swaps dead, geo-blocked or slow links for the fastest healthy alternative of the same `tvg-id` while preserving channel numbers, groups and logos, and rebuilds a pruned XMLTV guide from EPGShare01 country feeds (57 matched channels, 2.7 MB, against the 95 MB Spain-only DobleM guide that matched 5 of 405 channels). A per-minute guard restores the curated M3U and XML if Threadfin regenerates them empty on restart. The ffmpeg stream buffer is set to 3 MB with a 1500 ms timeout in RAM, and the player user-agent is `VLC/3.0.20 LibVLC/3.0.20`; playlists, EPG dumps and logs stay in `conf/`, outside Git.

### Non-Invasive Reverse Proxy UI Customization

Seerr's official image is not patched. A 32 MB `nginx:alpine` container named `seerr-theme` listens on `:5055` and proxies to `jellyseerr:5055` over the Docker network. Seerr itself publishes only `127.0.0.1:5056` on the host. Nginx clears `Accept-Encoding`, sets `sub_filter_once off`, and injects an 11.5 KB stylesheet before `</head>`. A `map` handles websocket upgrades; `X-Forwarded-*` headers preserve host, port and scheme.

Steady-state RSS is 5.5 to 5.6 MiB, well under the 32 MB cap. The container peaked at 14.8 MiB during startup. `docker compose pull` replaces Seerr without touching the theme. The CSS lives in `docker/media-stack/seerr-theme/html` and is mounted as a directory, so editing it and reloading Nginx is enough.

`5055` is the only Seerr entry point on the LAN. `5056` is loopback for diagnostics. Mapping `5055` on the Seerr container would make the proxy fail to bind, so that mapping stays out of the compose file.

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
│   ├── pihole/                   DNS sinkhole
│   │   ├── docker-compose.yml
│   │   └── .env.example
│   ├── portainer/                Docker management UI
│   │   └── docker-compose.yml
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
- Memory and CPU limits are explicit on containers that can grow: Immich 2 GB and 2 CPUs, PostgreSQL 768 MB and 1 CPU, Valkey 256 MB and 0.5 CPU, Immich ML 2560 MB and 6 CPUs, Threadfin 512 MB and 1.5 CPUs, Nginx 32 MB. Jellyfin and the bot run without hard caps because they need host devices or host networking.
- Secrets live in `.env` files excluded by Git. `.env.example` files contain placeholders only. No database, config directory, media file, model cache, playlist, EPG dump or log is tracked.
- Scripts use `set -euo pipefail`, log through `logger` and stdout, and return a non-zero status only when an operation that should have completed did not.
- Node 2 uses UUID mounts and never writes to the Kingston system SSD. The Toshiba HDD is the only data disk.
- Remote access has no inbound port forwards. Tailscale Funnel is disabled; Serve is tailnet-only.
- A history scan for `.env` files, private keys and common token formats returned no tracked secret. Threadfin playlists and EPG data stay in `conf/`, outside Git.

A quick health check from the administration host:

```bash
ssh homelab 'docker ps --format "table {{.Names}}\t{{.Status}}"'
ssh homelab 'sudo tailscale serve status'
ssh homelab 'ssh -i ~/.ssh/node2_bot mrdavid@192.168.1.4 "findmnt /mnt/storage && df -h /mnt/storage"'
```
