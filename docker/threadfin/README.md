# Threadfin

Live TV proxy for the Android TV in the living room, kept separate from Jellyfin.

## Runtime

The container listens on `34400` and runs with a `512 MB` memory limit and `1.5` CPU. Compose reads `TZ`, `PUID`, `PGID` and `THREADFIN_PORT` from `.env`; defaults are defined in `docker-compose.yml`.

Playback uses **direct provider URLs**. The M3U handed to Sparkle TV points at the IPTV edges, so Threadfin is not in the media path and adds no latency. The ffmpeg stream buffer (`3 MB`, `1500 ms`, kept in RAM) and the `VLC/3.0.20 LibVLC/3.0.20` user-agent are configured for the channels that are proxied through `/stream/`.

## Lineup

426 channels, numbering 2-647, eight groups:

| Group | Channels |
| --- | --- |
| República Dominicana | 211 |
| Internacionales - Cine y Series | 55 |
| Internacionales - Noticias | 35 |
| Internacionales - Novelas y Entretenimiento | 30 |
| Internacionales - Deportes | 30 |
| Internacionales - Infantil y Familiar | 25 |
| Internacionales - Música | 20 |
| Internacionales - Documentales y Cultura | 20 |

Canonical files live in `conf/data/`: `curated.m3u`, `curated.xml` and
`curated.xml.gz`. Threadfin serves them as `threadfin.m3u`, `threadfin.xml`
and `threadfin.xml.gz`.

## Daily sync (root cron)

`threadfin_sync.py` runs at **04:30** as root:

1. downloads the iptv-org RD and Spanish playlists as the alternative pool;
2. probes every channel with `curl` (HTTP 200) plus `ffprobe` **inside the
   container** (real video stream, 8 s cap, 10 parallel workers);
3. replaces dead, geo-blocked or slow links with the fastest healthy
   alternative of the same `tvg-id` (or the same normalized name). Only the
   URL line changes: channel number, group, `tvg-id` and logo stay untouched;
4. rebuilds a pruned XMLTV from EPGShare01 country guides
   (DO/MX/CO/AR/CL/PE/EC/ES), re-tagging every programme with our own `tvg-id`
   so Sparkle matches the guide by ID instead of by name;
5. publishes atomically and writes `logs/last_run.json`.

A per-minute guard re-applies `curated.*` over `threadfin.*` if Threadfin
regenerates them (it writes empty files on restart when XEPG has no channels).

Manual run:

```bash
sudo /home/mrdavid/HomeLab/docker/threadfin/threadfin_sync.sh --full
sudo tail -f /home/mrdavid/HomeLab/docker/threadfin/logs/sync.log
```

History keeps the last seven lineups in `conf/data/history/`. `logs/sync.log`
is bounded to ~4k lines. `conf/`, `temp/` and `logs/` stay out of Git.

## Sparkle TV client (TCL BeyondTV, 192.168.1.8)

- Playlist: `http://192.168.1.3:34400/m3u/threadfin.m3u`. The guide URL travels
  in the `url-tvg` header; add `http://192.168.1.3:34400/xmltv/threadfin.xml`
  manually only if the app does not pick it up.
- Settings -> Playback -> buffer: **10 s** (8-12 s is the useful range; drop to
  6 s if zapping feels slow).
- Video decoder: **Hardware**. Switch to software only for a channel that shows
  green blocks or no picture.
- Tunneled playback: ON only if the TV handles it, otherwise OFF.
- Audio passthrough: OFF (streams are AAC stereo).
- Auto frame rate: ON.
- General -> "Start on last channel" / "Resume last channel": ON.
- Parental controls: 4-digit PIN, lock settings and adult categories.
- Favourites: star the Dominican channels so the home screen opens on them.

## Notes

- The old DobleM guide was Spain-only and matched 5 of 405 channels; it was
  replaced by the pruned EPGShare01 build (57 channels, ~2.7 MB instead of
  95 MB). Dominican OTA channels without a public guide simply show no
  programme data.
- `apply_curated.sh` remains as a manual escape hatch; the cron uses
  `threadfin_sync.sh --publish` instead.
