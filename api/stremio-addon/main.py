import re
from concurrent.futures import ThreadPoolExecutor

import PTN
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


# --- Torrentio-style relevance filter -------------------------------------
# Trackers return a lot of noise: TV shows when you searched a movie, titles
# that merely *contain* the wanted word ("Far Away" for "Away"), foreign
# alt-title combos ("Projam aka Away"), and CAM rips. We parse the release
# name with PTN, then drop anything that doesn't match title + year.

_BAD_QUALITY_RE = re.compile(
    r"\b(CAM|HDCAM|CAMRip|HDTS|TS|TC|TELESYNC|TELECINE|KORSUB|HDTC)\b",
    re.IGNORECASE,
)
_LEADING_BRACKETS_RE = re.compile(r"^\s*(?:\[[^\]]*\]|\([^)]*\))\s*")
_ARTICLES = {"the", "a", "an"}


def _release_name(stream: dict) -> str:
    # Every scraper puts the raw release name on the first line of `title`.
    return (stream.get("title") or "").split("\n", 1)[0]


def _tokens(s: str) -> list:
    # Lowercase, replace non-alphanumerics with spaces, split. Keeps numbers
    # intact (years, sequel numbers like "Number 24").
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split()


def _strip_leading_brackets(name: str) -> str:
    # Releases often start with [Group] or (Encoder) prefixes; strip those
    # before checking what the actual title at position 0 is.
    while True:
        m = _LEADING_BRACKETS_RE.match(name)
        if not m:
            return name
        name = name[m.end():]


def _drop_leading_article(toks: list) -> list:
    return toks[1:] if toks and toks[0] in _ARTICLES else toks


def _matches_one_title(rel_toks: list, want_title: str,
                       want_year: int | None) -> bool:
    title_toks = _drop_leading_article(_tokens(want_title))
    if not title_toks:
        return False
    if rel_toks[:len(title_toks)] != title_toks:
        return False
    if want_year:
        idx = len(title_toks)
        valid_years = {str(want_year), str(want_year - 1), str(want_year + 1)}
        if idx >= len(rel_toks) or rel_toks[idx] not in valid_years:
            return False
    return True


def _matches_movie(name: str, titles: list, want_year: int | None) -> bool:
    # Reject TV: any release whose name parses as having a season or episode.
    p = PTN.parse(name)
    if p.get("season") is not None or p.get("episode") is not None:
        return False

    cleaned = _strip_leading_brackets(name)
    rel_toks = _drop_leading_article(_tokens(cleaned))
    if not rel_toks:
        return False

    # Accept the release if it matches ANY known title (primary, original,
    # or TMDB alternative). This is what catches "Nr. 24" (original Norwegian
    # title) and "Numero 24" (Italian alt) for the movie "Number 24".
    return any(_matches_one_title(rel_toks, t, want_year) for t in titles)


def _filter_movie_streams(streams: list, titles: list, want_year):
    titles = [t for t in (titles or []) if t]
    if not titles:
        return streams  # No metadata → can't safely filter.
    kept = []
    for s in streams:
        name = _release_name(s)
        if _BAD_QUALITY_RE.search(name):
            continue
        if _matches_movie(name, titles, want_year):
            kept.append(s)
    return kept


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


def _imdb_to_movie_info(imdb_id: str):
    """Resolve IMDB id → {title, year, titles[]} via TMDB.

    `titles` includes the primary title, original_title, and all
    `alternative_titles` — used to match foreign-language releases and
    AKAs (e.g. "Nr. 24" / "Numero 24" for "Number 24"). The list is
    deduplicated case-insensitively.
    """
    empty = {"title": None, "year": None, "titles": []}
    if not imdb_id or not imdb_id.startswith("tt"):
        return empty
    try:
        find = requests.get(
            f"{TMDB_BASE_URL}/find/{imdb_id.split(':')[0]}",
            params={"api_key": TMDB_API_KEY, "external_source": "imdb_id"},
            timeout=10,
        ).json()
    except Exception:
        return empty
    items = find.get("movie_results") or find.get("tv_results") or []
    if not items:
        return empty
    item = items[0]
    is_movie = "title" in item
    tmdb_id = item.get("id")
    primary = item.get("title") or item.get("name")
    date = item.get("release_date") or item.get("first_air_date") or ""
    year = int(date[:4]) if date[:4].isdigit() else None

    # One extra call to grab original_title + alternative_titles together.
    titles = [primary]
    try:
        kind = "movie" if is_movie else "tv"
        detail = requests.get(
            f"{TMDB_BASE_URL}/{kind}/{tmdb_id}",
            params={
                "api_key": TMDB_API_KEY,
                "append_to_response": "alternative_titles",
            },
            timeout=10,
        ).json()
        original = detail.get("original_title") or detail.get("original_name")
        if original:
            titles.append(original)
        alt = (detail.get("alternative_titles") or {}).get("titles") or []
        for a in alt:
            t = a.get("title")
            if t:
                titles.append(t)
    except Exception:
        pass

    # Dedupe case-insensitively, preserve order.
    seen = set()
    deduped = []
    for t in titles:
        k = (t or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            deduped.append(t.strip())
    return {"title": primary, "year": year, "titles": deduped}


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
    # Resolve TMDB once: primary title + alt titles for relevance filtering,
    # plus a "Title YEAR" string for scraper fallback queries.
    info = _imdb_to_movie_info(id)
    want_title = info["title"]
    want_year = info["year"]
    want_titles = info["titles"]
    _ty = (f"{want_title} {want_year}" if want_title and want_year
           else want_title)

    def title_year():
        return _ty

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

    all_streams = list(pb_streams) + list(lt_streams) + list(yts_streams) \
        + list(bs_streams) + list(kn_streams) + list(xx_streams)
    # Drop CAM rips, wrong-year, wrong-title, and TV-show leaks before dedupe.
    filtered = _filter_movie_streams(all_streams, want_titles, want_year)

    seen = {}
    for s in filtered:
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