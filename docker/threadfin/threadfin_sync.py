#!/usr/bin/env python3
"""
HomeLab Threadfin Sync
======================
Keeps the curated live-TV lineup (426 channels, custom numbering) healthy and
publishes a small, correctly-matched XMLTV guide.

What it does
------------
1. Downloads the upstream iptv-org playlists (RD + Spanish) used as the
   alternative pool.
2. Probes every channel of the curated lineup with ffprobe *inside* the
   threadfin container (real video stream, not just HTTP 200).
3. For dead / geo-blocked / slow channels it probes the best alternatives
   (same tvg-id first, then same normalized name) and swaps only the URL.
   Channel number, group, tvg-id, logo and display name are never touched.
4. Builds a pruned XMLTV from EPGShare01 country guides, re-tagging every
   programme with our own tvg-id so Sparkle TV matches by ID.
5. Publishes atomically (curated.* -> threadfin.*) and writes a JSON report.

Modes
-----
  --full      probe + rebuild EPG + publish   (daily cron)
  --publish   only re-apply curated -> threadfin if they differ (guard cron)
"""
import argparse
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from xml.etree import ElementTree as ET

BASE = "/home/mrdavid/HomeLab/docker/threadfin"
DATA = os.path.join(BASE, "conf", "data")
LOG_DIR = os.path.join(BASE, "logs")
HISTORY = os.path.join(DATA, "history")
CONTAINER = "threadfin"

SOURCES = {
    "rd": "https://iptv-org.github.io/iptv/countries/do.m3u",
    "es": "https://iptv-org.github.io/iptv/languages/spa.m3u",
}
# EPGShare01 country rippers (small, id-stable, LatAm + Spain coverage)
EPG_RIPPERS = ["DO1", "MX1", "CO1", "AR1", "CL1", "PE1", "EC1", "ES1"]
EPG_BASE = "https://epgshare01.online/epgshare01/epg_ripper_{}.xml.gz"

USER_AGENT = "VLC/3.0.20 LibVLC/3.0.20"
PROBE_TIMEOUT = 12          # hard cap per ffprobe (seconds)
PROBE_RW_TIMEOUT = 5_000_000  # ffprobe -rw_timeout (microseconds)
WORKERS = 10
MAX_ALTS = 3
MIN_ALIVE_RATIO = 0.60      # never publish a lineup where >40% died at once

NOISE = {"hd", "fhd", "sd", "uhd", "1080", "720", "4k", "8k", "tv", "canal",
         "channel", "en", "vivo", "live", "latino", "latinoamerica", "latin",
         "america", "de", "del", "la", "el", "los", "las", "y", "and", "the",
         "of", "plus", "mas",
         # region / feed qualifiers used by iptv-org names
         "colombia", "mexico", "argentina", "chile", "peru", "ecuador",
         "venezuela", "paraguay", "uruguay", "bolivia", "costa", "rica",
         "panama", "guatemala", "honduras", "salvador", "nicaragua", "cuba",
         "espana", "spain", "portugal", "brazil", "brasil", "usa", "us",
         "estados", "unidos", "international", "internacional", "signal",
         "senal", "feed", "east", "west", "north", "south", "central"}

# Known rebrands / spelling differences for the Dominican dial (core form)
ALIASES = {
    "antena7": "antenalatina",
    "digital15": "digitalquince",
    "canal4rd": "canal4",
    "telesistema11": "telesistema11",
    "teleantillas": "teleantillas",
    "colorvision": "colorvision",
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def core(s):
    c = "".join(t for t in norm(s).split() if t not in NOISE)
    return ALIASES.get(c, c)


def parse_m3u(path):
    """Return list of dicts preserving line structure."""
    entries, name, tid, logo, group = [], None, None, None, None
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    for line in lines:
        if line.startswith("#EXTINF"):
            tid = (re.search(r'tvg-id="([^"]*)"', line) or [None, ""])[1]
            logo = (re.search(r'tvg-logo="([^"]*)"', line) or [None, ""])[1]
            group = (re.search(r'group-title="([^"]*)"', line) or [None, ""])[1]
            name = line.rsplit(",", 1)[-1].strip()
        elif line and not line.startswith("#"):
            entries.append({"id": tid, "name": name, "logo": logo,
                            "group": group, "url": line})
    return lines, entries


def fetch(url, dest, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return dest


# --------------------------------------------------------------------------- #
# probing
# --------------------------------------------------------------------------- #
def probe(url):
    """Probe a stream inside the threadfin container.

    The whole check runs under `timeout` *inside* the container: killing the
    docker-exec client does not kill the child, which previously left orphan
    ffprobe processes and drove the host load average to 22.
    Returns (ok, ms, info).
    """
    script = (
        'code=$(curl -s -o /dev/null -m 5 -A "$UA" -w "%{http_code}" "$URL"); '
        'case "$code" in '
        '200|206|301|302|307|308) ;; '
        '*) echo "HTTP:$code"; exit 1;; '
        'esac; '
        'timeout -k 2 8 ffprobe -v error -user_agent "$UA" '
        '-rw_timeout 5000000 -timeout 5000000 '
        '-analyzeduration 3000000 -probesize 1500000 '
        '-select_streams v:0 -show_entries stream=codec_name,width,height '
        '-of csv=p=0 "$URL"'
    )
    cmd = ["docker", "exec", "-e", f"URL={url}", "-e", f"UA={USER_AGENT}",
           CONTAINER, "sh", "-c", script]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=18)
        ms = int((time.time() - t0) * 1000)
        out = (r.stdout or "").strip()
        if out.startswith("HTTP:"):
            return False, ms, out
        first = out.splitlines()[0] if out else ""
        if r.returncode == 0 and first.strip(", "):
            return True, ms, first
        return False, ms, ""
    except subprocess.TimeoutExpired:
        return False, 18000, "timeout"
    except Exception as e:  # noqa: BLE001
        return False, -1, str(e)[:60]


def probe_many(urls):
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        return list(ex.map(probe, urls))


# --------------------------------------------------------------------------- #
# lineup validation
# --------------------------------------------------------------------------- #
def build_pool(provider_files):
    """index provider streams by base tvg-id and by normalized core name."""
    by_id, by_name = {}, {}
    for path in provider_files:
        if not os.path.exists(path):
            continue
        _, entries = parse_m3u(path)
        for e in entries:
            base = (e["id"] or "").split("@")[0].lower()
            if base:
                by_id.setdefault(base, []).append(e["url"])
            c = core(e["name"])
            if len(c) >= 5:
                by_name.setdefault(c, []).append(e["url"])
    return by_id, by_name


def validate_lineup(lines, entries, by_id, by_name, report):
    log(f"probing {len(entries)} live channels...")
    results = probe_many([e["url"] for e in entries])
    alive = {i for i, (ok, _, _) in enumerate(results) if ok}
    report["channels_total"] = len(entries)
    report["channels_alive_initial"] = len(alive)

    dead = [i for i in range(len(entries)) if i not in alive]
    log(f"alive: {len(alive)}/{len(entries)}  dead: {len(dead)}")

    # build alternatives for dead channels
    alt_map = {}
    for i in dead:
        e = entries[i]
        base = (e["id"] or "").split("@")[0].lower()
        c = core(e["name"])
        cands = []
        for u in by_id.get(base, []):
            if u != e["url"]:
                cands.append(u)
        if len(cands) < MAX_ALTS and len(c) >= 5:
            for u in by_name.get(c, []):
                if u != e["url"] and u not in cands:
                    cands.append(u)
        alt_map[i] = cands[:MAX_ALTS]

    flat, owner = [], []
    for i, cands in alt_map.items():
        for u in cands:
            flat.append(u)
            owner.append(i)
    log(f"probing {len(flat)} alternatives for {len(alt_map)} dead channels...")
    ares = probe_many(flat) if flat else []

    best = {}

    def rank(info, ms):
        """Quality first within a startup budget, then speed.

        info is 'codec,width,height'. A 1080p feed that takes 9 s to open is
        worse for zapping than a 720p feed at 2 s, so HD only wins inside 6 s.
        """
        parts = (info or "").split(",")
        codec = parts[0] if parts else ""
        h = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        fast_hd = 1 if (h >= 720 and ms <= 6000) else 0
        return (fast_hd, 1 if codec == "h264" else 0, h, -ms)

    for (ok, ms, info), i, u in zip(ares, owner, flat):
        if not ok:
            continue
        if i not in best or rank(info, ms) > rank(best[i][2], best[i][1]):
            best[i] = (u, ms, info)

    replaced, unrecovered = 0, []
    url_line_idx = [n for n, l in enumerate(lines)
                    if l and not l.startswith("#")]
    for i in dead:
        if i in best:
            url, ms, info = best[i]
            entries[i]["url"] = url
            lines[url_line_idx[i]] = url
            replaced += 1
            log(f"  swap #{i} {entries[i]['name']}: {ms} ms {info}")
        else:
            unrecovered.append(entries[i]["name"])

    report["channels_replaced"] = replaced
    report["channels_unrecovered"] = unrecovered
    report["channels_alive_final"] = len(alive) + replaced
    report["probe_ms_avg"] = int(sum(r[1] for r in results if r[0]) / max(1, len(alive)))
    log(f"replaced {replaced} channels, {len(unrecovered)} still without alternative")
    return lines


# --------------------------------------------------------------------------- #
# EPG
# --------------------------------------------------------------------------- #
def epg_brand(display_name):
    """Extract the brand from an EPGShare01 display name.

    'Canal 5 de República Dominicana (Telemicro)' -> 'telemicro'
    'Canal A&E (República Dominicana)'           -> 'ae'
    'Canal CDN Deportes'                          -> 'cdndeportes'
    """
    n = display_name.strip()
    m = re.findall(r"\(([^)]*)\)", n)
    if m:
        inner = m[-1]
        if not re.search(r"rep[uú]blica|dominican|m[eé]xico|colombia|argentina|"
                         r"chile|per[uú]|ecuador|espa[nñ]a|venezuela", inner, re.I):
            n = inner
        else:
            n = re.sub(r"\([^)]*\)", " ", n)
    n = re.sub(r"^\s*canal\s+(\d+\s*)?(de\s+)?", " ", n, flags=re.I)
    n = re.sub(r"^\s*\d+\s*", " ", n)
    return core(n)


def build_epg(entries, report):
    tmp = tempfile.mkdtemp(prefix="epg_")
    epg_chans = {}   # channel-id -> {"names": set(), "progs": list}
    progs_by_ch = {}
    try:
        for cc in EPG_RIPPERS:
            gz = os.path.join(tmp, f"{cc}.xml.gz")
            try:
                fetch(EPG_BASE.format(cc), gz, timeout=90)
                with gzip.open(gz, "rb") as f:
                    data = f.read()
                root = ET.fromstring(data)
            except Exception as e:  # noqa: BLE001
                log(f"  EPG {cc} skipped: {e}")
                continue
            n_ch = n_pr = 0
            for ch in root.findall("channel"):
                cid = ch.get("id")
                names = [d.text or "" for d in ch.findall("display-name")]
                epg_chans.setdefault(cid, set()).update(names)
                n_ch += 1
            for pr in root.findall("programme"):
                cid = pr.get("channel")
                progs_by_ch.setdefault(cid, []).append(pr)
                n_pr += 1
            log(f"  EPG {cc}: {n_ch} channels / {n_pr} programmes")
            root.clear()

        # brand index: brand -> set of channel-ids (all country guides)
        brand_idx = {}
        for cid, names in epg_chans.items():
            for nm in names:
                b = epg_brand(nm)
                if len(b) >= 3:
                    brand_idx.setdefault(b, set()).add(cid)

        def pick_country(hits, country):
            """Only accept an EPG id from the channel's own country guide.

            Cross-country guessing produced wrong guides (Antena 7 -> TEN.es),
            so we stay conservative. Exception: US-licensed cable networks are
            carried on the Dominican grid, so .us falls back to .do (exact
            brand only) when no US guide exists.
            """
            if not country:
                return hits.pop() if len(hits) == 1 else None
            local = {h for h in hits if h.lower().endswith("." + country)}
            if len(local) == 1:
                return local.pop()
            if not local and country == "us":
                do = {h for h in hits if h.lower().endswith(".do")}
                if len(do) == 1:
                    return do.pop()
            return None

        wanted = {}
        for e in entries:
            if not e["id"]:
                continue
            country = e["id"].split("@")[0].rsplit(".", 1)[-1].lower()
            if len(country) != 2:
                country = ""
            b = core(e["name"])
            hits = set(brand_idx.get(b, set()))
            chosen = pick_country(hits, country)
            if chosen:
                wanted[e["id"]] = chosen

        log(f"EPG matched {len(wanted)} of {len({e['id'] for e in entries})} tvg-ids")
        matched_names = [e["name"] for e in entries if e["id"] in wanted]
        log("EPG channels: " + ", ".join(dict.fromkeys(matched_names))[:1800])

        out = ET.Element("tv", {"generator-info-name": "HomeLab Threadfin Sync"})
        emitted = 0
        seen = set()
        total_progs = 0
        for e in entries:
            cid = e["id"]
            if cid not in wanted or cid in seen:
                continue
            seen.add(cid)
            src = wanted[cid]
            ch = ET.SubElement(out, "channel", {"id": cid})
            ET.SubElement(ch, "display-name").text = e["name"]
            if e["logo"]:
                ET.SubElement(ch, "icon", {"src": e["logo"]})
            progs = progs_by_ch.get(src, [])
            for pr in progs:
                pr.set("channel", cid)
                out.append(pr)
            total_progs += len(progs)
            emitted += 1

        xml_path = os.path.join(DATA, "curated.xml.new")
        ET.ElementTree(out).write(xml_path, encoding="utf-8", xml_declaration=True)
        with open(xml_path, "rb") as f:
            raw = f.read()
        with gzip.open(os.path.join(DATA, "curated.xml.gz.new"), "wb", compresslevel=9) as f:
            f.write(raw)
        report["epg_channels"] = emitted
        report["epg_programmes"] = total_progs
        report["epg_bytes"] = len(raw)
        report["epg_map"] = {e["id"]: {"name": e["name"], "epg": wanted[e["id"]]}
                             for e in entries if e["id"] in wanted}
        log(f"EPG written: {emitted} channels, {len(raw)/1e6:.1f} MB")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# publish
# --------------------------------------------------------------------------- #
def publish(report):
    """Atomically promote curated.* to threadfin.*"""
    pairs = [("curated.m3u", "threadfin.m3u"),
             ("curated.xml", "threadfin.xml"),
             ("curated.xml.gz", "threadfin.xml.gz")]
    for src, dst in pairs:
        s = os.path.join(DATA, src)
        d = os.path.join(DATA, dst)
        if os.path.exists(s):
            shutil.copy2(s, d + ".tmp")
            os.replace(d + ".tmp", d)
    report["published_at"] = datetime.now().isoformat(timespec="seconds")
    log("published curated.* -> threadfin.*")


def publish_if_needed():
    changed = []
    for src, dst in [("curated.m3u", "threadfin.m3u"),
                     ("curated.xml", "threadfin.xml"),
                     ("curated.xml.gz", "threadfin.xml.gz")]:
        s, d = os.path.join(DATA, src), os.path.join(DATA, dst)
        if not os.path.exists(s):
            continue
        if not os.path.exists(d) or os.path.getsize(s) != os.path.getsize(d):
            changed.append((s, d))
    for s, d in changed:
        shutil.copy2(s, d + ".tmp")
        os.replace(d + ".tmp", d)
    return len(changed)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--epg-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0,
                    help="probe only the first N channels (smoke test)")
    args = ap.parse_args()
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(HISTORY, exist_ok=True)

    if args.publish and not args.full:
        n = publish_if_needed()
        if n:
            log(f"guard: re-applied {n} file(s) overwritten by threadfin")
        return 0

    report = {"started": datetime.now().isoformat(timespec="seconds")}
    m3u_path = os.path.join(DATA, "curated.m3u")
    if not os.path.exists(m3u_path):
        log("ERROR: curated.m3u missing")
        return 2

    # 1. fresh alternative pool
    providers = []
    for key, url in SOURCES.items():
        dest = os.path.join(DATA, f"src_{key}.m3u")
        try:
            fetch(url, dest, timeout=90)
            providers.append(dest)
            log(f"downloaded {key} playlist")
        except Exception as e:  # noqa: BLE001
            log(f"WARN: {key} playlist unavailable ({e}); using cached copy")
            cached = os.path.join(DATA, f"src_{key}.m3u")
            if os.path.exists(cached):
                providers.append(cached)
    by_id, by_name = build_pool(providers)

    # 2. validate + swap URLs
    lines, entries = parse_m3u(m3u_path)
    if len(entries) < 50:
        log(f"ERROR: curated.m3u only has {len(entries)} channels, aborting")
        return 2

    if args.epg_only:
        build_epg(entries, report)
        os.replace(os.path.join(DATA, "curated.xml.new"),
                   os.path.join(DATA, "curated.xml"))
        os.replace(os.path.join(DATA, "curated.xml.gz.new"),
                   os.path.join(DATA, "curated.xml.gz"))
        publish(report)
        with open(os.path.join(LOG_DIR, "last_run.json"), "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        return 0

    probe_entries = entries[:args.limit] if args.limit else entries
    lines = validate_lineup(lines, probe_entries, by_id, by_name, report)

    if not args.limit and report["channels_alive_final"] < MIN_ALIVE_RATIO * len(entries):
        log("ERROR: too many dead channels, refusing to publish (keeping old files)")
        return 3

    # 3. EPG
    build_epg(entries, report)

    # 4. atomic publish + history
    shutil.copy2(m3u_path, m3u_path + ".prev")
    new_m3u = m3u_path + ".new"
    with open(new_m3u, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(new_m3u, m3u_path)
    os.replace(os.path.join(DATA, "curated.xml.new"), os.path.join(DATA, "curated.xml"))
    os.replace(os.path.join(DATA, "curated.xml.gz.new"), os.path.join(DATA, "curated.xml.gz"))
    publish(report)

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    shutil.copy2(os.path.join(DATA, "curated.m3u"),
                 os.path.join(HISTORY, f"curated_{stamp}.m3u"))
    for old in sorted(os.listdir(HISTORY))[:-7]:
        os.remove(os.path.join(HISTORY, old))

    report["finished"] = datetime.now().isoformat(timespec="seconds")
    with open(os.path.join(LOG_DIR, "last_run.json"), "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    log("done: " + json.dumps({k: report[k] for k in
                               ("channels_total", "channels_alive_final",
                                "channels_replaced", "epg_channels")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
