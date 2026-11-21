import asyncio
from urllib.parse import urlparse

import httpx


async def _check_http(url: str, timeout: float = 1.5) -> bool:
    try:
        async with httpx.AsyncClient(
            verify=False,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "HomeLab-Nexus/1.0"},
        ) as client:
            resp = await client.get(url)
            # Any HTTP response means the service is reachable.
            return resp.status_code < 500
    except Exception:
        return False


async def _check_tcp(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        conn = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(conn, timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def check_target(target: str, timeout: float = 1.5) -> str:
    target = (target or "").strip()
    if not target:
        return "offline"
    parsed = urlparse(target)
    scheme = (parsed.scheme or "").lower()

    if scheme in ("http", "https"):
        ok = await _check_http(target, timeout=timeout)
        return "online" if ok else "offline"

    if scheme in ("tcp", "ssh"):
        host = parsed.hostname
        port = parsed.port or (22 if scheme == "ssh" else None)
        if not host or not port:
            return "offline"
        ok = await _check_tcp(host, port, timeout=timeout)
        return "online" if ok else "offline"

    # Fallback: try as HTTP
    ok = await _check_http(target, timeout=timeout)
    return "online" if ok else "offline"


async def check_many(items: list[dict], timeout: float = 1.5) -> dict[int, str]:
    async def one(item: dict) -> tuple[int, str]:
        target = item.get("health_url") or item.get("lan_url") or ""
        status = await check_target(target, timeout=timeout)
        return int(item["id"]), status

    results = await asyncio.gather(*(one(i) for i in items), return_exceptions=True)
    out: dict[int, str] = {}
    for i, res in enumerate(results):
        item_id = int(items[i]["id"])
        if isinstance(res, Exception):
            out[item_id] = "offline"
        else:
            out[item_id] = res[1]
    return out
