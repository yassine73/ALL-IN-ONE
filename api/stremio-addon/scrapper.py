import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, urljoin

import requests


_RESOLUTION_RE = re.compile(r"\b(2160p|1440p|1080p|720p|480p|360p)\b", re.IGNORECASE)
_RESOLUTION_HINTS = (
    (re.compile(r"\b(4k|uhd)\b", re.IGNORECASE), "2160p"),
    (re.compile(r"\b(fhd|fullhd)\b", re.IGNORECASE), "1080p"),
    (re.compile(r"\bhd\b", re.IGNORECASE), "720p"),
)
_QUALITY_RE = re.compile(
    r"\b(REMUX|BluRay|BDRip|BRRip|WEB[- ]?DL|WEBRip|HDRip|DVDRip|HDTV|CAM|TS|TC)\b",
    re.IGNORECASE,
)
_CODEC_RE = re.compile(r"\b(x265|x264|h\.?265|h\.?264|HEVC|AVC|AV1)\b", re.IGNORECASE)
_AUDIO_RE = re.compile(
    r"\b(DDP?5\.1|DD\+|DTS(?:-HD)?(?:\.MA)?|TrueHD|Atmos|AAC|AC3|FLAC|MP3|Opus)\b",
    re.IGNORECASE,
)
_HDR_RE = re.compile(r"\b(HDR10\+?|HDR|DV|Dolby ?Vision)\b", re.IGNORECASE)

# Public trackers verified responsive via UDP BEP-15 connect handshake.
# Ordered by measured latency (lowest first) so clients try the fastest first.
# Deduped: same IP+port and same-server-different-port collapsed to one entry.
# Re-verify periodically — dead trackers add latency without benefit.
_TRACKERS = [
    "udp://tracker.torrent.eu.org:451/announce",        # ~44 ms
    "udp://54.36.179.216:6969/announce",                # ~46 ms
    "udp://135.125.236.64:6969/announce",               # ~49 ms
    "udp://tracker.auctor.tv:6969/announce",            # ~53 ms
    "udp://107.189.4.235:1337/announce",                # ~55 ms
    "udp://5.255.124.190:6969/announce",                # ~55 ms
    "udp://107.189.7.165:6969/announce",                # ~56 ms
    "udp://185.171.202.111:6969/announce",              # ~60 ms
    "udp://tracker.filemail.com:6969/announce",         # ~60 ms
    "udp://37.120.182.83:54123/announce",               # ~65 ms (also serves buddyfly.top/torrentclub.space)
    "udp://tracker.srv00.com:6969/announce",            # ~74 ms
    "udp://87.106.210.134:6969/announce",               # ~80 ms
    "udp://88.80.22.67:2710/announce",                  # ~83 ms
    "udp://81.230.84.201:6969/announce",                # ~84 ms
    "udp://open.stealth.si:80/announce",                # ~85 ms
    "udp://185.146.233.150:6969/announce",              # ~87 ms
    "udp://torrents.artixlinux.org:6969/announce",      # ~92 ms
    "udp://tracker.opentrackr.org:1337/announce",       # ~94 ms
    "udp://94.72.140.51:6969/announce",                 # ~95 ms
    "udp://34.66.57.33:1337/announce",                  # ~135 ms
    "udp://192.3.130.53:1337/announce",                 # ~141 ms
    "udp://94.136.190.183:1337/announce",               # ~148 ms
    "udp://23.175.184.30:23333/announce",               # ~164 ms
    "udp://209.141.59.25:6969/announce",                # ~184 ms
    "udp://tracker.theoks.net:6969/announce",           # ~185 ms
    "udp://189.18.162.12:6969/announce",                # ~205 ms
    "udp://explodie.org:6969/announce",                 # ~214 ms
    "udp://wepzone.net:6969/announce",                  # ~251 ms
    "udp://open.demonii.com:1337/announce",             # ~304 ms
    "udp://tracker.dler.com:6969/announce",             # ~333 ms
]


def _format_size(num_bytes):
    try:
        n = float(num_bytes)
    except (TypeError, ValueError):
        return None
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"


def _first_match(pattern, text):
    m = pattern.search(text)
    return m.group(0) if m else None


def _extract_resolution(name: str):
    m = _RESOLUTION_RE.search(name)
    if m:
        return m.group(1).lower()
    for pattern, value in _RESOLUTION_HINTS:
        if pattern.search(name):
            return value
    return None


def scrape_piratebay(query: str):
    try:
        res = requests.get(
            "https://apibay.org/q.php",
            params={"q": query, "cat": "0"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        data = res.json()
    except Exception:
        return []

    results = []
    for obj in data:
        infohash = obj.get("info_hash")
        if not infohash or infohash == "0" * 40:
            continue

        name = obj.get("name")
        if not name:
            continue

        try:
            seeders = int(obj.get("seeders", 0))
        except (TypeError, ValueError):
            seeders = 0
        try:
            leechers = int(obj.get("leechers", 0))
        except (TypeError, ValueError):
            leechers = 0

        size_bytes = int(obj.get("size") or 0) or None
        size_str = _format_size(obj.get("size"))
        resolution = _extract_resolution(name)
        quality = _first_match(_QUALITY_RE, name)
        codec = _first_match(_CODEC_RE, name)
        audio = _first_match(_AUDIO_RE, name)
        hdr = _first_match(_HDR_RE, name)
        uploader = obj.get("username") or "anonymous"

        tag_line = " | ".join(t for t in (resolution, quality, codec, hdr, audio) if t)
        stats = f"👤 {seeders} / {leechers}"
        if size_str:
            stats += f"  💾 {size_str}"
        stats += f"  🏴‍☠️ {uploader}"

        title_parts = [name]
        if tag_line:
            title_parts.append(tag_line)
        title_parts.append(stats)

        stream_name = "ThePirateBay"
        if resolution:
            stream_name += f"\n{resolution}"

        results.append({
            "name": stream_name,
            "title": "\n".join(title_parts),
            "infoHash": infohash,
            "fileIdx": 0,
            "sources": [f"tracker:{t}" for t in _TRACKERS] + [f"dht:{infohash}"],
            "behaviorHints": {
                "bingeGroup": f"piratebay|{resolution or 'unknown'}",
                "videoSize": size_bytes,
                "filename": name,
            },
            "_seeders": seeders,
        })

    return results


# --- LimeTorrents ---------------------------------------------------------

_LIME_BASE = "https://limetorrent.in"
_LIME_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
_LIME_ROW_RE = re.compile(
    r'<tr[^>]*>\s*<td class="tdleft">.*?'
    r'<div class="tt-name">.*?<a\s+href="(?P<href>/post-detail/[^"]+)"[^>]*>(?P<name>.*?)</a>.*?'
    r'<td class="tdnormal">(?P<date>[^<]*)</td>\s*'
    r'<td class="tdnormal">(?P<size>[^<]*)</td>\s*'
    r'<td class="tdseed">(?P<seed>[^<]*)</td>\s*'
    r'<td class="tdleech">(?P<leech>[^<]*)</td>',
    re.DOTALL,
)
_LIME_HASH_RE = re.compile(r"itorrents\.org/torrent/([A-Fa-f0-9]{40})\.torrent", re.IGNORECASE)
_LIME_MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:([A-Fa-f0-9]{40})", re.IGNORECASE)
_SIZE_UNIT_BYTES = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}


def _parse_size_to_bytes(size_str: str):
    m = re.match(r"\s*([\d.]+)\s*([KMGT]?B)\s*", size_str, re.IGNORECASE)
    if not m:
        return None
    try:
        return int(float(m.group(1)) * _SIZE_UNIT_BYTES[m.group(2).upper()])
    except (KeyError, ValueError):
        return None


def _lime_fetch_hash(session, detail_url):
    try:
        r = session.get(detail_url, timeout=15)
        body = r.text
    except Exception:
        return None
    m = _LIME_HASH_RE.search(body) or _LIME_MAGNET_RE.search(body)
    return m.group(1).lower() if m else None


def scrape_limetorrents(query: str, max_results: int = 20):
    if not query:
        return []

    search_url = urljoin(_LIME_BASE, f"/get-posts/keywords:{quote(query)}/")
    session = requests.Session()
    session.headers.update({"User-Agent": _LIME_UA})

    try:
        res = session.get(search_url, timeout=20)
        page = res.text
    except Exception:
        return []

    rows = []
    for m in _LIME_ROW_RE.finditer(page):
        name = html.unescape(re.sub(r"<[^>]+>", "", m.group("name"))).strip()
        if not name:
            continue
        try:
            seeders = int(m.group("seed").strip() or 0)
            leechers = int(m.group("leech").strip() or 0)
        except ValueError:
            seeders = leechers = 0
        rows.append({
            "name": name,
            "detail_url": urljoin(_LIME_BASE, m.group("href")),
            "size_str": m.group("size").strip(),
            "seeders": seeders,
            "leechers": leechers,
        })
        if len(rows) >= max_results:
            break

    if not rows:
        return []

    with ThreadPoolExecutor(max_workers=min(8, len(rows))) as ex:
        future_to_row = {ex.submit(_lime_fetch_hash, session, r["detail_url"]): r for r in rows}
        for fut in as_completed(future_to_row):
            future_to_row[fut]["infohash"] = fut.result()

    results = []
    for row in rows:
        infohash = row.get("infohash")
        if not infohash:
            continue

        name = row["name"]
        size_bytes = _parse_size_to_bytes(row["size_str"])
        resolution = _extract_resolution(name)
        quality = _first_match(_QUALITY_RE, name)
        codec = _first_match(_CODEC_RE, name)
        audio = _first_match(_AUDIO_RE, name)
        hdr = _first_match(_HDR_RE, name)

        tag_line = " | ".join(t for t in (resolution, quality, codec, hdr, audio) if t)
        stats = f"👤 {row['seeders']} / {row['leechers']}"
        if row["size_str"]:
            stats += f"  💾 {row['size_str']}"
        stats += "  🍋 LimeTorrents"

        title_parts = [name]
        if tag_line:
            title_parts.append(tag_line)
        title_parts.append(stats)

        stream_name = "LimeTorrents"
        if resolution:
            stream_name += f"\n{resolution}"

        results.append({
            "name": stream_name,
            "title": "\n".join(title_parts),
            "infoHash": infohash,
            "fileIdx": 0,
            "sources": [f"tracker:{t}" for t in _TRACKERS] + [f"dht:{infohash}"],
            "behaviorHints": {
                "bingeGroup": f"limetorrents|{resolution or 'unknown'}",
                "videoSize": size_bytes,
                "filename": name,
            },
            "_seeders": row["seeders"],
        })

    return results
