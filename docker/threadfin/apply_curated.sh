#!/usr/bin/env bash
set -euo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
cp "$BASE/conf/data/curated.m3u" "$BASE/conf/data/threadfin.m3u"
cp "$BASE/conf/data/curated.xml"  "$BASE/conf/data/threadfin.xml"
cp "$BASE/conf/data/curated.xml.gz" "$BASE/conf/data/threadfin.xml.gz"
echo "curated threadfin outputs applied"
