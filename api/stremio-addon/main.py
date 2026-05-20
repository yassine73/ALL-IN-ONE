from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers.tmdb import (
    TMDB_API_KEY,
    TMDB_BASE_URL
)
from scrapper import (
    ensure_trackers,
    scrape_limetorrents,
    scrape_piratebay,
    scrape_nyaa_erai,
    scrape_yts,
    scrape_bitsearch,
    scrape_knaben,
    scrape_1337x,
)
import requests


def _cinemeta_absolute_episode(imdb_id: str, season: int, episode: int):
    """Resolve (season, episode) → absolute episode number via Cinemeta.

    Anime release groups (like Erai-raws) name files by absolute episode,
    while Stremio sends per-season numbers. Returns None on failure.
    """
    try:
        r = requests.get(
            f"https://v3-cinemeta.strem.io/meta/series/{imdb_id}.json",
            timeout=15,
        )
        videos = (r.json() or {}).get("meta", {}).get("videos") or []
    except Exception:
        return None
    if not videos:
        return None
    # Sum episodes in prior real seasons (season > 0), then add this episode.
    prior = sum(1 for v in videos if 0 < (v.get("season") or 0) < season)
    return prior + episode if prior or season == 1 else None


def _tmdb_japanese_romaji_titles(tmdb_id: int):
    """Return Japanese alt titles that look like romaji (ASCII).

    Erai-raws names releases in romaji (e.g. "Shingeki no Kyojin"), but
    TMDB's primary `name` is usually the English localized title. Pull the
    JP alt titles and keep the ASCII ones — those are the romaji forms.
    """
    try:
        r = requests.get(
            f"{TMDB_BASE_URL}/tv/{tmdb_id}/alternative_titles",
            params={"api_key": TMDB_API_KEY},
            timeout=10,
        )
        results = (r.json() or {}).get("results") or []
    except Exception:
        return []
    romaji = []
    for entry in results:
        if entry.get("iso_3166_1") != "JP":
            continue
        t = entry.get("title") or ""
        if t and t.isascii() and any(c.isalpha() for c in t):
            romaji.append(t)
    return romaji


def _imdb_to_tv_info(imdb_id: str):
    """Resolve IMDB id → TV show info dict.

    Returns: {name, original_name, romaji_titles, is_anime} or None.
    `romaji_titles` is a list of likely-romaji aliases (e.g. AoT → "Shingeki
    no Kyojin"), needed because anime release groups use romaji.
    """
    if not imdb_id or not imdb_id.startswith("tt"):
        return None
    try:
        r = requests.get(
            f"{TMDB_BASE_URL}/find/{imdb_id}",
            params={"api_key": TMDB_API_KEY, "external_source": "imdb_id"},
            timeout=10,
        )
        tv_results = (r.json() or {}).get("tv_results") or []
    except Exception:
        return None
    if not tv_results:
        return None
    show = tv_results[0]
    name = show.get("name") or show.get("original_name")
    original_name = show.get("original_name") or name
    origin_countries = show.get("origin_country") or []
    original_language = show.get("original_language") or ""
    genre_ids = show.get("genre_ids") or []
    # Anime heuristic: Japanese origin + Animation genre (TMDB id 16).
    is_anime = (
        ("JP" in origin_countries or original_language == "ja")
        and 16 in genre_ids
    )
    romaji_titles = _tmdb_japanese_romaji_titles(show["id"]) if is_anime else []
    return {
        "name": name,
        "original_name": original_name,
        "romaji_titles": romaji_titles,
        "is_anime": is_anime,
    }


def _imdb_to_title_year(imdb_id: str):
    """Resolve IMDB id → 'Title YEAR' via TMDB. Returns None if not found."""
    if not imdb_id or not imdb_id.startswith("tt"):
        return None
    try:
        r = requests.get(
            f"{TMDB_BASE_URL}/find/{imdb_id.split(':')[0]}",
            params={"api_key": TMDB_API_KEY, "external_source": "imdb_id"},
            timeout=10,
        )
        data = r.json()
    except Exception:
        return None
    for key in ("movie_results", "tv_results"):
        items = data.get(key) or []
        if not items:
            continue
        item = items[0]
        title = item.get("title") or item.get("name")
        date = item.get("release_date") or item.get("first_air_date") or ""
        year = date[:4] if date else ""
        if title and year:
            return f"{title} {year}"
        return title
    return None


def _scrape_with_fallback(scraper, imdb_id: str, fallback_query):
    """Try scraper with imdb id; if empty, retry with title+year."""
    results = scraper(imdb_id)
    if results:
        return results
    fallback = fallback_query() if callable(fallback_query) else fallback_query
    if fallback and fallback != imdb_id:
        return scraper(fallback)
    return []

app = FastAPI()

# Stremio loads addons via XHR from app.strem.io and the desktop webview;
# without permissive CORS the manifest fetch silently fails on macOS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ADDON_ID = "com.myaddonlocal.test3"

# ----------------------------
# 1. MANIFEST
# ----------------------------
@app.get("/manifest.json")
def manifest():
    return {
        "id": ADDON_ID,
        "version": "1.0.3",
        "name": "My Addon",
        "description": "Movies & Series, animes",
        "resources": ["stream"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt"],
        "catalogs": [],
    }

# ----------------------------
# 2. STREAM (PLAY LINKS)
# ----------------------------
@app.get("/stream/movie/{id}.json")
def stream(id: str):
    ensure_trackers()
    # Cache the TMDB resolution so we only hit it once even if both scrapers fall back.
    _cached_title = {}

    def title_year():
        if "v" not in _cached_title:
            _cached_title["v"] = _imdb_to_title_year(id)
        return _cached_title["v"]

    with ThreadPoolExecutor(max_workers=6) as ex:
        f_pb = ex.submit(_scrape_with_fallback, scrape_piratebay, id, title_year)
        f_lt = ex.submit(_scrape_with_fallback, scrape_limetorrents, id, title_year)
        f_yts = ex.submit(_scrape_with_fallback, scrape_yts, id, title_year)
        f_bs = ex.submit(_scrape_with_fallback, scrape_bitsearch, id, title_year)
        f_kn = ex.submit(_scrape_with_fallback, scrape_knaben, id, title_year)
        f_xx = ex.submit(_scrape_with_fallback, scrape_1337x, id, title_year)
        pb_streams = f_pb.result()
        lt_streams = f_lt.result()
        yts_streams = f_yts.result()
        bs_streams = f_bs.result()
        kn_streams = f_kn.result()
        xx_streams = f_xx.result()

    seen = {}
    for s in (*pb_streams, *lt_streams, *yts_streams, *bs_streams, *kn_streams, *xx_streams):
        h = (s.get("infoHash") or "").lower()
        if not h:
            continue
        # On duplicates, keep the entry with the higher seed count.
        if h not in seen or s.get("_seeders", 0) > seen[h].get("_seeders", 0):
            seen[h] = s

    merged = sorted(seen.values(), key=lambda s: s.get("_seeders", 0), reverse=True)
    for s in merged:
        s.pop("_seeders", None)
        # Don't pin fileIdx for movies — most movie torrents are single-file,
        # and for multi-file folder releases Stremio's player will pick the
        # largest video automatically once metadata comes in from the swarm.
        # Hard-coding 0 is what made multi-file releases open the wrong file.
        s.pop("fileIdx", None)

    return {"streams": merged}


# Example: /stream/series/tt0131179:3:14.json
@app.get("/stream/series/{id}.json")
def stream_series(id: str):
    ensure_trackers()
    parts = id.split(":")
    if len(parts) != 3:
        return {"streams": []}
    imdb_id, season_str, episode_str = parts
    try:
        season = int(season_str)
        episode = int(episode_str)
    except ValueError:
        return {"streams": []}

    info = _imdb_to_tv_info(imdb_id)
    if not info:
        return {"streams": []}

    streams = []
    if info["is_anime"]:
        absolute = _cinemeta_absolute_episode(imdb_id, season, episode)
        # Try in order: romaji (Erai-raws' native naming, e.g. "Shingeki no
        # Kyojin"), then the English TMDB name, then the original Japanese
        # name (rarely matches but cheap to attempt last).
        candidates = []
        for c in [*info.get("romaji_titles", []),
                  info.get("name"), info.get("original_name")]:
            if c and c not in candidates:
                candidates.append(c)
        for candidate in candidates:
            streams = scrape_nyaa_erai(
                candidate, season, episode, absolute_episode=absolute
            )
            if streams:
                break

    for s in streams:
        s.pop("_seeders", None)
    return {"streams": streams}