import html
import random
import re
import socket
import statistics
import struct
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, urlparse

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

# Trackers are pulled from ngosang/trackerslist (mirrored across three URLs),
# probed (BEP-15 UDP connect), and the fastest _TRACKER_TOP_N working ones
# are passed to Stremio. Kept short on purpose: every entry is tried for
# every stream, so a dead/slow tracker delays peer discovery. The torrent's
# own trackers (from its magnet URL) supply the long tail on top of these.
_TRACKER_LIST_URLS = (
    "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt",
    "https://ngosang.github.io/trackerslist/trackers_best.txt",
    "https://cdn.jsdelivr.net/gh/ngosang/trackerslist@master/trackers_best.txt",
)
_TRACKER_TOP_N = 5
_TRACKER_CACHE_TTL = 600  # seconds
_TRACKER_PROBE_TIMEOUT = 3.0
_TRACKER_PROBE_SAMPLES = 2
_TRACKER_LIST_FETCH_TIMEOUT = 8
_BEP15_MAGIC = 0x41727101980

_tracker_cache_lock = threading.Lock()
_tracker_cache = {"trackers": [], "expires_at": 0.0}


def _probe_tracker_once(host: str, port: int) -> float | None:
    tid = random.randint(0, 0xFFFFFFFF)
    pkt = struct.pack("!QII", _BEP15_MAGIC, 0, tid)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(_TRACKER_PROBE_TIMEOUT)
    try:
        s.connect((host, port))
        t0 = time.perf_counter()
        s.send(pkt)
        data = s.recv(64)
        dt = (time.perf_counter() - t0) * 1000.0
    except Exception:
        return None
    finally:
        s.close()
    if len(data) < 16:
        return None
    action, rtid = struct.unpack("!II", data[:8])
    if action != 0 or rtid != tid:
        return None
    return dt


def _probe_tracker(url: str):
    p = urlparse(url)
    if not p.hostname or not p.port:
        return url, None
    try:
        addrs = socket.getaddrinfo(p.hostname, p.port, type=socket.SOCK_DGRAM)
        host = addrs[0][4][0]
    except Exception:
        return url, None
    samples = []
    for _ in range(_TRACKER_PROBE_SAMPLES):
        rtt = _probe_tracker_once(host, p.port)
        if rtt is not None:
            samples.append(rtt)
    if not samples:
        return url, None
    return url, statistics.median(samples)


def _load_tracker_candidates():
    """Fetch the tracker list from ngosang/trackerslist, trying mirrors in order."""
    for url in _TRACKER_LIST_URLS:
        try:
            r = requests.get(url, timeout=_TRACKER_LIST_FETCH_TIMEOUT)
            if r.status_code != 200 or not r.text.strip():
                continue
            body = r.text
        except Exception:
            continue
        out = []
        seen = set()
        for line in body.splitlines():
            u = line.strip()
            if not u or u.startswith("#") or u in seen:
                continue
            seen.add(u)
            out.append(u)
        if out:
            return out
    return []


def _get_trackers():
    """Return the fastest working trackers.

    Fetches the candidate list from ngosang/trackerslist and probes them,
    caching the result for _TRACKER_CACHE_TTL seconds.
    """
    now = time.time()
    with _tracker_cache_lock:
        cached = _tracker_cache
        if cached["trackers"] and cached["expires_at"] > now:
            return list(cached["trackers"])

    candidates = _load_tracker_candidates()
    if not candidates:
        return []

    with ThreadPoolExecutor(max_workers=min(32, len(candidates))) as ex:
        results = list(ex.map(_probe_tracker, candidates))
    alive = [(u, ms) for (u, ms) in results if ms is not None]
    alive.sort(key=lambda r: r[1])
    top = [u for (u, _ms) in alive[:_TRACKER_TOP_N]]

    with _tracker_cache_lock:
        _tracker_cache["trackers"] = top
        _tracker_cache["expires_at"] = now + _TRACKER_CACHE_TTL

    return list(top)


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


def scrape_piratebay(query: str, max_results: int = 20):
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
        infohash = (obj.get("info_hash") or "").lower()
        # Stremio requires lowercase; apibay returns uppercase. Also drop the
        # "no results" sentinel row (all-zero hash, name="No results returned").
        if not infohash or len(infohash) != 40 or infohash == "0" * 40:
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
            "sources": [f"tracker:{t}" for t in _get_trackers()] + [f"dht:{infohash}"],
            "_seeders": seeders,
        })

    results.sort(key=lambda s: s.get("_seeders", 0), reverse=True)
    return results[:max_results]


# --- Nyaa (anime) ---------------------------------------------------------

_NYAA_BASE = "https://nyaa.si"
_NYAA_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"

# Erai-raws release name patterns. Examples:
#   [Erai-raws] Detective Conan - 1124 [1080p][Multiple Subtitle]...
#   [Erai-raws] Detective Conan - 0001 ~ 0123 [1080p]...      (range)
#   [Erai-raws] Some Show - S02 [1080p]                        (whole season)
#   [Erai-raws] Some Show Season 2 [Batch][1080p]              (whole season)
_ERAI_SINGLE_RE = re.compile(r"-\s*(\d{1,4})(?:v\d+)?\s*(?:\[|$)")
_ERAI_RANGE_RE = re.compile(r"-\s*(\d{1,4})\s*~\s*(\d{1,4})")
_ERAI_SEASON_RE = re.compile(r"\b(?:S(?:eason)?\s*0*(\d{1,2}))\b", re.IGNORECASE)
_ERAI_BATCH_RE = re.compile(r"\bbatch\b", re.IGNORECASE)
_ERAI_TAG_RE = re.compile(r"\[Erai-raws\]", re.IGNORECASE)


def _classify_erai_release(name: str):
    """Parse a release title into a structured descriptor.

    Returns a dict:
      {"kind": "range",  "low": int, "high": int, "season": int|None}
      {"kind": "single", "n": int,                "season": int|None}
      {"kind": "season", "season": int}
      {"kind": "batch"}
      {"kind": None}

    A `season` field on range/single means the title explicitly mentions
    "Season N" — the numbers should be interpreted per-season, not absolute.
    """
    season_m = _ERAI_SEASON_RE.search(name)
    season_n = int(season_m.group(1)) if season_m else None

    rng = _ERAI_RANGE_RE.search(name)
    if rng:
        return {"kind": "range", "low": int(rng.group(1)),
                "high": int(rng.group(2)), "season": season_n}

    is_batch = bool(_ERAI_BATCH_RE.search(name))
    single = _ERAI_SINGLE_RE.search(name)
    if single and not is_batch:
        return {"kind": "single", "n": int(single.group(1)), "season": season_n}
    if season_n is not None:
        return {"kind": "season", "season": season_n}
    if is_batch:
        return {"kind": "batch"}
    return {"kind": None}


def _release_matches(classified, season: int, episode: int, absolute_episode: int):
    """Decide if a classified release covers the requested episode.

    Two numbering schemes need handling:
      - per-season (e.g. Shingeki no Kyojin Season 3 - 01 ~ 12)
      - absolute  (e.g. Detective Conan - 0754 ~ 1132)
    A release that explicitly tags "Season N" uses per-season numbering when
    N matches the request; otherwise the numbers are treated as absolute.
    """
    kind = classified["kind"]
    if kind is None:
        return False
    if kind == "batch":
        return True

    rel_season = classified.get("season")
    if rel_season is not None and rel_season != season:
        # Release belongs to a different season — never matches.
        return False
    # If the release tags our season, numbers are per-season; otherwise absolute.
    use_episode = episode if rel_season == season else absolute_episode

    if kind == "single":
        return classified["n"] == use_episode
    if kind == "range":
        return classified["low"] <= use_episode <= classified["high"]
    if kind == "season":
        return True  # season tag already matched above
    return False


_NYAA_USER_ROW_RE = re.compile(
    r'<tr class="(?:default|success|danger)">'
    r'.*?<a href="/view/(?P<id>\d+)"\s+title="(?P<title>[^"]+)"'
    r'.*?magnet:\?xt=urn:btih:(?P<hash>[A-Fa-f0-9]{40})'
    r'.*?<td class="text-center">(?P<size>[^<]+)</td>'
    r'\s*<td class="text-center"[^>]*>[^<]+</td>'
    r'\s*<td class="text-center">(?P<seeders>\d+)</td>'
    r'\s*<td class="text-center">(?P<leechers>\d+)</td>',
    re.DOTALL,
)


def _nyaa_user_fetch_page(user: str, query: str, page: int):
    """Fetch one HTML page of a Nyaa user's uploads. Returns parsed rows.

    The /user/<name> path lists every upload by that user, including older
    batches that the global search index quietly drops. The RSS variant of
    this path returns nothing, so we parse the HTML.
    """
    url = f"{_NYAA_BASE}/user/{quote(user)}?q={quote(query)}&c=1_2&p={page}"
    try:
        res = requests.get(url, headers={"User-Agent": _NYAA_UA}, timeout=25)
        body = res.text
    except Exception:
        return []

    rows = []
    for m in _NYAA_USER_ROW_RE.finditer(body):
        rows.append({
            "view_id": m.group("id"),
            "title": html.unescape(m.group("title")).strip(),
            "infohash": m.group("hash").lower(),
            "size_str": m.group("size").strip(),
            "seeders": int(m.group("seeders")),
            "leechers": int(m.group("leechers")),
        })
    return rows


def _nyaa_user_paginated(user: str, query: str, max_pages: int = 15):
    """Yield rows across pages, stopping at first short/empty page."""
    for page in range(1, max_pages + 1):
        rows = _nyaa_user_fetch_page(user, query, page)
        if not rows:
            return
        for r in rows:
            yield r
        if len(rows) < 70:  # Nyaa serves ~75/page; short page = last page
            return


_VIDEO_EXTS = (".mkv", ".mp4", ".avi", ".m4v", ".mov", ".webm")


def _bdecode(data: bytes, pos: int = 0):
    """Minimal bencode decoder. Returns (value, next_pos)."""
    c = data[pos:pos + 1]
    if c == b"i":
        end = data.index(b"e", pos)
        return int(data[pos + 1:end]), end + 1
    if c == b"l":
        out = []
        pos += 1
        while data[pos:pos + 1] != b"e":
            v, pos = _bdecode(data, pos)
            out.append(v)
        return out, pos + 1
    if c == b"d":
        out = {}
        pos += 1
        while data[pos:pos + 1] != b"e":
            k, pos = _bdecode(data, pos)
            v, pos = _bdecode(data, pos)
            out[k] = v
        return out, pos + 1
    # string: <len>:<bytes>
    colon = data.index(b":", pos)
    length = int(data[pos:colon])
    start = colon + 1
    return data[start:start + length], start + length


def _torrent_file_list(view_id: str):
    """Fetch a Nyaa torrent and return its ordered file list.

    Returns a list of dicts: [{"index": i, "name": basename, "size": bytes}].
    Single-file torrents return one entry with index 0.
    """
    try:
        r = requests.get(
            f"{_NYAA_BASE}/download/{view_id}.torrent",
            headers={"User-Agent": _NYAA_UA},
            timeout=30,
        )
        if r.status_code != 200 or not r.content.startswith(b"d"):
            return []
        meta, _ = _bdecode(r.content)
    except Exception:
        return []

    info = meta.get(b"info") or {}
    files = info.get(b"files")
    if not files:
        # Single-file torrent
        name = (info.get(b"name") or b"").decode("utf-8", "replace")
        size = info.get(b"length") or 0
        return [{"index": 0, "name": name, "size": size}] if name else []

    out = []
    for i, f in enumerate(files):
        path_parts = [p.decode("utf-8", "replace") for p in (f.get(b"path") or [])]
        name = path_parts[-1] if path_parts else ""
        out.append({"index": i, "name": name, "size": f.get(b"length") or 0})
    return out


def _resolve_episode_file_idx(view_id: str, absolute_episode: int):
    """Find the file index inside a multi-file torrent matching `absolute_episode`.

    Matches the episode number with any zero-padding (1/01/001/0001), bounded
    so it isn't a substring of a larger number. Among matches, prefers video
    file extensions and the largest size as a tiebreaker. Returns None if
    no confident match.
    """
    files = _torrent_file_list(view_id)
    if not files:
        return None
    if len(files) == 1:
        return 0

    # Episode number, possibly zero-padded, surrounded by non-digit boundaries.
    # Strip bracketed tags first ([1080p], [Multiple Subtitle], [CRC32-hash])
    # so digits inside CRC32 checksums aren't mistaken for episode numbers.
    bracket_re = re.compile(r"\[[^\]]*\]")
    ep_re = re.compile(rf"(?<!\d)0*{absolute_episode}(?!\d)")

    candidates = []
    for f in files:
        name = f["name"]
        cleaned = bracket_re.sub(" ", name)
        if not ep_re.search(cleaned):
            continue
        is_video = name.lower().endswith(_VIDEO_EXTS)
        candidates.append((is_video, f["size"], f["index"], name))

    if not candidates:
        return None
    # Prefer videos, then largest size.
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return candidates[0][2]


def scrape_nyaa_erai(title: str, season: int, episode: int,
                     absolute_episode: int = None,
                     max_results: int = 1500, max_pages_per_query: int = 20):
    """Search Nyaa for [Erai-raws] releases of `title` matching the episode.

    `episode` is the per-season episode; `absolute_episode` (if known) is the
    absolute episode number used by anime release groups. Matches single
    episodes, combined ranges (0001 ~ 0123), full seasons, and batches.

    Paginates each query — Nyaa serves ~75 items per RSS page, so a long-
    running anime's older releases require walking past page 1.
    """
    if not title:
        return []

    # Scrape directly from the Erai-raws user-uploads page rather than the
    # global Nyaa search. The global search index quietly omits some older
    # batches (e.g. "Detective Conan - 0001 ~ 0123" never surfaces under
    # `[Erai-raws] Detective Conan`), but they're all on /user/Erai-raws.
    # Filter as we paginate and stop once we have enough matches to avoid
    # walking the full feed for long-running series.
    abs_ep = absolute_episode if absolute_episode is not None else episode
    enough = 6  # stop once we have this many matches across qualities

    matched_rows = []
    seen_hashes = set()
    scanned = 0
    for row in _nyaa_user_paginated("Erai-raws", title, max_pages=max_pages_per_query):
        scanned += 1
        if scanned > max_results:
            break
        ih = row["infohash"]
        if ih in seen_hashes:
            continue
        seen_hashes.add(ih)

        name = row["title"]
        if not _ERAI_TAG_RE.search(name):
            continue
        classified = _classify_erai_release(name)
        if not _release_matches(classified, season, episode, abs_ep):
            continue
        matched_rows.append((row, classified))
        if len(matched_rows) >= enough:
            break

    # Resolve fileIdx in parallel for any non-single torrent — Stremio needs
    # it to pick the right episode out of a batch/range release. Skip for
    # single-episode torrents (always index 0). For each torrent, search using
    # the right episode number (per-season vs absolute).
    needs_idx = []
    for i, (row, cls) in enumerate(matched_rows):
        if cls["kind"] == "single":
            continue
        rel_season = cls.get("season")
        ep_for_file = episode if rel_season == season else abs_ep
        needs_idx.append((i, row["view_id"], ep_for_file))

    file_idx_by_pos = {}
    if needs_idx:
        with ThreadPoolExecutor(max_workers=min(6, len(needs_idx))) as ex:
            futs = {
                ex.submit(_resolve_episode_file_idx, vid, ep_for_file): pos
                for pos, vid, ep_for_file in needs_idx
            }
            for fut in as_completed(futs):
                file_idx_by_pos[futs[fut]] = fut.result()

    results = []
    for pos, (row, classified) in enumerate(matched_rows):
        name = row["title"]
        infohash = row["infohash"]
        seeders = row["seeders"]
        leechers = row["leechers"]
        size_str = row["size_str"]
        size_bytes = _parse_size_to_bytes(size_str) if size_str else None

        resolution = _extract_resolution(name)
        codec = _first_match(_CODEC_RE, name)
        audio = _first_match(_AUDIO_RE, name)
        hdr = _first_match(_HDR_RE, name)

        kind = classified["kind"]
        if kind == "single":
            kind_tag = f"E{classified['n']:02d}"
        elif kind == "range":
            kind_tag = f"E{classified['low']:02d}-E{classified['high']:02d}"
        elif kind == "season":
            kind_tag = f"S{classified['season']:02d}"
        elif kind == "batch":
            kind_tag = "BATCH"
        else:
            kind_tag = None

        tag_line = " | ".join(t for t in (resolution, kind_tag, codec, hdr, audio) if t)
        stats = f"👤 {seeders} / {leechers}"
        if size_str:
            stats += f"  💾 {size_str}"
        stats += "  🌸 Erai-raws"

        title_parts = [name]
        if tag_line:
            title_parts.append(tag_line)
        title_parts.append(stats)

        stream_name = "Nyaa"
        if resolution:
            stream_name += f"\n{resolution}"

        results.append({
            "name": stream_name,
            "title": "\n".join(title_parts),
            "infoHash": infohash,
            "fileIdx": 0 if kind == "single" else file_idx_by_pos.get(pos),
            "sources": [f"tracker:{t}" for t in _get_trackers()] + [f"dht:{infohash}"],
            "behaviorHints": {
                "bingeGroup": f"nyaa-erai|{resolution or 'unknown'}",
                "videoSize": size_bytes,
                "filename": name,
            },
            "_seeders": seeders,
        })

    results.sort(key=lambda s: s.get("_seeders", 0), reverse=True)
    return results


# --- YTS ------------------------------------------------------------------

_YTS_API = "https://movies-api.accel.li/api/v2/list_movies.json"
_YTS_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


def scrape_yts(query: str, max_results: int = 20):
    """Search YTS for movie torrents. `query` may be an IMDB id (ttNNNNNNN)
    or a free-text title — YTS's `query_term` accepts both.

    Each movie returns multiple torrents (720p/1080p/2160p/3D); we emit one
    stream per torrent so Stremio can show quality choices.
    """
    if not query:
        return []

    try:
        res = requests.get(
            _YTS_API,
            params={"query_term": query, "limit": min(max_results, 50)},
            headers={"User-Agent": _YTS_UA},
            timeout=15,
        )
        data = res.json()
    except Exception:
        return []

    movies = ((data or {}).get("data") or {}).get("movies") or []
    if not movies:
        return []

    results = []
    for movie in movies:
        title = movie.get("title_long") or movie.get("title") or ""
        for t in movie.get("torrents") or []:
            infohash = (t.get("hash") or "").lower()
            if not infohash or len(infohash) != 40:
                continue

            quality = t.get("quality") or ""           # "720p", "1080p", "2160p", "3D"
            type_ = t.get("type") or ""                # "bluray", "web", etc.
            codec = t.get("video_codec") or ""         # "x264", "x265"
            size_str = t.get("size") or ""
            size_bytes = t.get("size_bytes")
            try:
                seeders = int(t.get("seeds") or 0)
                leechers = int(t.get("peers") or 0)
            except (TypeError, ValueError):
                seeders = leechers = 0

            resolution = quality.lower() if _RESOLUTION_RE.match(quality or "") else None

            tag_line = " | ".join(p for p in (quality, type_.upper() if type_ else None, codec) if p)
            stats = f"👤 {seeders} / {leechers}"
            if size_str:
                stats += f"  💾 {size_str}"
            stats += "  🎬 YTS"

            release_name = f"{title} [{quality}] [{type_}] [YTS]".strip()
            title_parts = [release_name]
            if tag_line:
                title_parts.append(tag_line)
            title_parts.append(stats)

            stream_name = "YTS"
            if resolution:
                stream_name += f"\n{resolution}"

            results.append({
                "name": stream_name,
                "title": "\n".join(title_parts),
                "infoHash": infohash,
                "fileIdx": 0,
                "sources": [f"tracker:{tr}" for tr in _get_trackers()] + [f"dht:{infohash}"],
                "behaviorHints": {
                    "videoSize": size_bytes,
                    "filename": release_name,
                },
                "_seeders": seeders,
            })

    results.sort(key=lambda s: s.get("_seeders", 0), reverse=True)
    return results[:max_results]


# --- Knaben (meta-aggregator) --------------------------------------------

_KNABEN_API = "https://api.knaben.org/v1"
_KNABEN_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
_KNABEN_MOVIE_CATS = [3000000]  # Movies tree (3001000 = HD, 3002000 = SD, etc.)
_MAGNET_TR_RE = re.compile(r"[?&]tr=([^&]+)")


def _magnet_trackers(magnet_url: str):
    """Pull `tr=` trackers out of a magnet URL, URL-decoded."""
    if not magnet_url:
        return []
    from urllib.parse import unquote
    return [unquote(m.group(1)) for m in _MAGNET_TR_RE.finditer(magnet_url)]


def scrape_knaben(query: str, max_results: int = 20, categories=None):
    """Search knaben.org's meta-aggregator (indexes TPB, YTS, RuTracker, …).

    Server-side sort by seeders desc; client-side resort + truncate as a
    safety net.
    """
    if not query:
        return []

    body = {
        "query": query,
        "search_type": "100%",
        "search_field": "title",
        "order_by": "seeders",
        "order_direction": "desc",
        "size": min(max(max_results, 1), 100),
        "categories": categories or _KNABEN_MOVIE_CATS,
    }

    try:
        res = requests.post(
            _KNABEN_API,
            json=body,
            headers={"User-Agent": _KNABEN_UA, "Content-Type": "application/json"},
            timeout=8,
        )
        data = res.json()
    except Exception:
        return []

    results = []
    for obj in (data or {}).get("hits") or []:
        infohash = (obj.get("hash") or "").lower()
        if not infohash or len(infohash) != 40 or infohash == "0" * 40:
            continue

        name = obj.get("title") or ""
        if not name:
            continue

        try:
            seeders = int(obj.get("seeders") or 0)
            leechers = int(obj.get("peers") or 0)
        except (TypeError, ValueError):
            seeders = leechers = 0

        size_bytes = obj.get("bytes") or None
        size_str = _format_size(size_bytes) if size_bytes else ""
        tracker = obj.get("tracker") or obj.get("cachedOrigin") or "Knaben"

        # Critical: include the torrent's own trackers from its magnet URL.
        # Knaben aggregates from many sites and each row carries its source
        # site's trackers, where the actual seeders are announcing. Without
        # these, peer discovery falls back to DHT-only.
        own_trackers = _magnet_trackers(obj.get("magnetUrl") or "")
        sources = (
            [f"tracker:{t}" for t in own_trackers]
            + [f"tracker:{t}" for t in _get_trackers()]
            + [f"dht:{infohash}"]
        )
        resolution = _extract_resolution(name)
        quality = _first_match(_QUALITY_RE, name)
        codec = _first_match(_CODEC_RE, name)
        audio = _first_match(_AUDIO_RE, name)
        hdr = _first_match(_HDR_RE, name)

        tag_line = " | ".join(t for t in (resolution, quality, codec, hdr, audio) if t)
        stats = f"👤 {seeders} / {leechers}"
        if size_str:
            stats += f"  💾 {size_str}"
        stats += f"  🧭 Knaben · {tracker}"

        title_parts = [name]
        if tag_line:
            title_parts.append(tag_line)
        title_parts.append(stats)

        stream_name = "Knaben"
        if resolution:
            stream_name += f"\n{resolution}"

        results.append({
            "name": stream_name,
            "title": "\n".join(title_parts),
            "infoHash": infohash,
            "fileIdx": 0,
            "sources": sources,
            "_seeders": seeders,
        })

    results.sort(key=lambda s: s.get("_seeders", 0), reverse=True)
    return results[:max_results]


# --- BitSearch (ex-SolidTorrents) ----------------------------------------

_BITSEARCH_API = "https://bitsearch.eu/api/v1/search"
_BITSEARCH_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


def scrape_bitsearch(query: str, max_results: int = 20):
    """Search bitsearch.eu (DHT-sourced). Asks the API to sort by seeders
    descending server-side so the truncation keeps the best torrents.
    """
    if not query:
        return []
    try:
        res = requests.get(
            _BITSEARCH_API,
            params={
                "q": query,
                "sort": "seeders",
                "order": "desc",
                "category": 1,  # Movies
                "limit": min(max(max_results, 1), 100),
            },
            headers={"User-Agent": _BITSEARCH_UA},
            timeout=15,
        )
        data = res.json()
    except Exception:
        return []

    if not (data or {}).get("success"):
        return []

    results = []
    for obj in data.get("results") or []:
        infohash = (obj.get("infohash") or "").lower()
        if not infohash or len(infohash) != 40 or infohash == "0" * 40:
            continue

        name = obj.get("title") or ""
        if not name:
            continue

        try:
            seeders = int(obj.get("seeders") or 0)
            leechers = int(obj.get("leechers") or 0)
        except (TypeError, ValueError):
            seeders = leechers = 0

        size_bytes = obj.get("size") or None
        size_str = _format_size(size_bytes) if size_bytes else ""
        resolution = _extract_resolution(name)
        quality = _first_match(_QUALITY_RE, name)
        codec = _first_match(_CODEC_RE, name)
        audio = _first_match(_AUDIO_RE, name)
        hdr = _first_match(_HDR_RE, name)

        tag_line = " | ".join(t for t in (resolution, quality, codec, hdr, audio) if t)
        stats = f"👤 {seeders} / {leechers}"
        if size_str:
            stats += f"  💾 {size_str}"
        stats += "  🔎 BitSearch"

        title_parts = [name]
        if tag_line:
            title_parts.append(tag_line)
        title_parts.append(stats)

        stream_name = "BitSearch"
        if resolution:
            stream_name += f"\n{resolution}"

        results.append({
            "name": stream_name,
            "title": "\n".join(title_parts),
            "infoHash": infohash,
            "fileIdx": 0,
            "sources": [f"tracker:{t}" for t in _get_trackers()] + [f"dht:{infohash}"],
            "_seeders": seeders,
        })

    results.sort(key=lambda s: s.get("_seeders", 0), reverse=True)
    return results[:max_results]


# --- LimeTorrents ---------------------------------------------------------

_LIME_BASE = "https://www.limetorrents.fun"
_LIME_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
_LIME_ENCLOSURE_HASH_RE = re.compile(r"/torrent/([A-Fa-f0-9]{40})\.torrent", re.IGNORECASE)
_LIME_SEEDS_LEECH_RE = re.compile(r"Seeds?:\s*(\d+)\s*,\s*Leechers?\s*(\d+)", re.IGNORECASE)
_SIZE_UNIT_BYTES = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}


def _parse_size_to_bytes(size_str: str):
    m = re.match(r"\s*([\d.]+)\s*([KMGT]?B)\s*", size_str, re.IGNORECASE)
    if not m:
        return None
    try:
        return int(float(m.group(1)) * _SIZE_UNIT_BYTES[m.group(2).upper()])
    except (KeyError, ValueError):
        return None


def _format_bytes(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.2f} {unit}" if unit != "B" else f"{int(f)} B"
        f /= 1024
    return f"{n} B"


def scrape_limetorrents(query: str, max_results: int = 20):
    if not query:
        return []

    rss_url = f"{_LIME_BASE}/searchrss/{quote(query)}/"

    try:
        res = requests.get(rss_url, headers={"User-Agent": _LIME_UA}, timeout=20)
        body = res.content
    except Exception:
        return []

    end = body.rfind(b"</rss>")
    if end != -1:
        body = body[: end + len(b"</rss>")]

    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []

    rows = []
    for item in root.iterfind(".//item"):
        title_el = item.find("title")
        name = (title_el.text or "").strip() if title_el is not None else ""
        if not name:
            continue

        infohash = None
        enclosure = item.find("enclosure")
        if enclosure is not None:
            m = _LIME_ENCLOSURE_HASH_RE.search(enclosure.get("url", ""))
            if m:
                infohash = m.group(1).lower()
        if not infohash:
            continue

        size_el = item.find("size")
        try:
            size_bytes = int((size_el.text or "").strip()) if size_el is not None else 0
        except ValueError:
            size_bytes = 0

        seeders = leechers = 0
        desc_el = item.find("description")
        if desc_el is not None and desc_el.text:
            m = _LIME_SEEDS_LEECH_RE.search(desc_el.text)
            if m:
                seeders = int(m.group(1))
                leechers = int(m.group(2))

        rows.append({
            "name": html.unescape(name),
            "infohash": infohash,
            "size_str": _format_bytes(size_bytes) if size_bytes else "",
            "seeders": seeders,
            "leechers": leechers,
        })

    if not rows:
        return []

    rows.sort(key=lambda r: r["seeders"], reverse=True)
    rows = rows[:max_results]

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
            "sources": [f"tracker:{t}" for t in _get_trackers()] + [f"dht:{infohash}"],
            "_seeders": row["seeders"],
        })

    results.sort(key=lambda s: s.get("_seeders", 0), reverse=True)
    return results


# --- 1337x -----------------------------------------------------------------
# The primary 1337x.to domain sits behind a Cloudflare JS challenge that can't
# be cleared without a real browser — unworkable on Termux. The 1377x.to mirror
# serves the same content over plain HTTPS, so we hit it directly. It is slow
# (commonly 5-15s) and occasionally times out; one retry is enough.

_X1337_BASE = "https://www.1377x.to"
_X1337_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
_X1337_ROW_RE = re.compile(
    r'<tr>\s*<td[^>]*class="coll-1 name"[^>]*>'
    r'(?:\s*<a[^>]*class="icon"[^>]*>.*?</a>\s*)*'
    r'<a\s+href="(?P<href>/torrent/[^"]+)"[^>]*>(?P<name>.*?)</a>\s*</td>\s*'
    r'<td[^>]*class="coll-2 seeds"[^>]*>(?P<seed>\d+)</td>\s*'
    r'<td[^>]*class="coll-3 leeches"[^>]*>(?P<leech>\d+)</td>\s*'
    r'<td[^>]*class="coll-date"[^>]*>[^<]*</td>\s*'
    r'<td[^>]*class="coll-4 size[^"]*"[^>]*>(?P<size>.*?)</td>',
    re.DOTALL,
)
_X1337_MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:([A-Fa-f0-9]{40})", re.IGNORECASE)
_X1337_TOKEN_RE = re.compile(r"[a-z0-9]+")
_X1337_STOPWORDS = {"the", "and", "of", "a", "an", "in", "on", "to", "for", "with", "or"}


def _x1337_query_tokens(query: str):
    return [
        t for t in _X1337_TOKEN_RE.findall(query.lower())
        if len(t) >= 2 and t not in _X1337_STOPWORDS
    ]


def _x1337_title_matches(name: str, tokens):
    if not tokens:
        return True
    name_tokens = set(_X1337_TOKEN_RE.findall(name.lower()))
    return all(t in name_tokens for t in tokens)


def _x1337_get(session, url, timeout=30, retries=1):
    for attempt in range(retries + 1):
        try:
            return session.get(url, timeout=timeout).text
        except requests.RequestException:
            if attempt == retries:
                return None


def _x1337_fetch_hash(session, detail_url):
    body = _x1337_get(session, detail_url)
    if not body:
        return None
    m = _X1337_MAGNET_RE.search(body)
    return m.group(1).lower() if m else None


def scrape_1337x(query: str, max_results: int = 20, category: str = "Movies"):
    if not query:
        return []

    query_tokens = _x1337_query_tokens(query)
    if not query_tokens:
        return []

    # 1337x search treats some short words ("and", "the", …) as operators
    # and returns OR-matches sorted by global seeders — feeding the raw title
    # often surfaces top-seeded movies that share only a stopword or the year.
    # Send the tokenised query (stopwords already stripped) instead.
    site_query = " ".join(query_tokens)
    search_url = f"{_X1337_BASE}/sort-category-search/{quote(site_query)}/{category}/seeders/desc/1/"
    session = requests.Session()
    session.headers.update({"User-Agent": _X1337_UA})

    page = _x1337_get(session, search_url, timeout=30, retries=1)
    if not page:
        return []

    rows = []
    for m in _X1337_ROW_RE.finditer(page):
        name = html.unescape(re.sub(r"<[^>]+>", "", m.group("name"))).strip()
        if not name:
            continue
        if not _x1337_title_matches(name, query_tokens):
            continue
        size_str = html.unescape(re.sub(r"<[^>]+>", "", m.group("size"))).strip()
        try:
            seeders = int(m.group("seed"))
            leechers = int(m.group("leech"))
        except ValueError:
            seeders = leechers = 0
        rows.append({
            "name": name,
            "detail_url": _X1337_BASE + m.group("href"),
            "size_str": size_str,
            "seeders": seeders,
            "leechers": leechers,
        })

    if not rows:
        return []

    rows = rows[:max_results]

    with ThreadPoolExecutor(max_workers=min(6, len(rows))) as ex:
        future_to_row = {ex.submit(_x1337_fetch_hash, session, r["detail_url"]): r for r in rows}
        for fut in as_completed(future_to_row):
            future_to_row[fut]["infohash"] = fut.result()

    results = []
    for row in rows:
        infohash = row.get("infohash")
        if not infohash:
            continue

        name = row["name"]
        resolution = _extract_resolution(name)
        quality = _first_match(_QUALITY_RE, name)
        codec = _first_match(_CODEC_RE, name)
        audio = _first_match(_AUDIO_RE, name)
        hdr = _first_match(_HDR_RE, name)

        tag_line = " | ".join(t for t in (resolution, quality, codec, hdr, audio) if t)
        stats = f"👤 {row['seeders']} / {row['leechers']}"
        if row["size_str"]:
            stats += f"  💾 {row['size_str']}"
        stats += "  🎯 1337x"

        title_parts = [name]
        if tag_line:
            title_parts.append(tag_line)
        title_parts.append(stats)

        stream_name = "1337x"
        if resolution:
            stream_name += f"\n{resolution}"

        results.append({
            "name": stream_name,
            "title": "\n".join(title_parts),
            "infoHash": infohash,
            "fileIdx": 0,
            "sources": [f"tracker:{t}" for t in _get_trackers()] + [f"dht:{infohash}"],
            "_seeders": row["seeders"],
        })

    results.sort(key=lambda s: s.get("_seeders", 0), reverse=True)
    return results
