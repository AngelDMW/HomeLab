# Threadfin

Live TV proxy for Android TV, kept separate from Jellyfin.

## Runtime

The container listens on `34400` and runs with a `512 MB` memory limit and `1.5` CPU. Compose reads `TZ`, `PUID`, `PGID` and `THREADFIN_PORT` from `.env`; defaults are defined in `docker-compose.yml`.

Playlists and the XMLTV guide are managed from the Threadfin web UI. Public sources used in this deployment:

- `https://iptv-org.github.io/iptv/countries/do.m3u`
- `https://iptv-org.github.io/iptv/languages/spa.m3u`
- `https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml.gz`

No private provider URL or credential is stored in this directory.

## State and curation

Threadfin writes its database, downloaded playlists, EPG cache and authentication file to `conf/`. The generated guide is large, so `conf/` and `temp/` are ignored by Git. Only `docker-compose.yml`, `.env.example`, `apply_curated.sh` and this file are versioned.

The curated M3U, XML and gzip outputs live in `conf/data/curated.*`. Threadfin regenerates its own outputs on restart or after an XEPG cycle. Restore the curated channel list with:

```bash
bash /home/mrdavid/HomeLab/docker/threadfin/apply_curated.sh
```

The script copies `curated.m3u`, `curated.xml` and `curated.xml.gz` over `threadfin.m3u`, `threadfin.xml` and `threadfin.xml.gz`. Run it after a container restart if the channel order or selection changes.
