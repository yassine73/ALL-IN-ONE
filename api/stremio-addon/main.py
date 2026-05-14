from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from routers.tmdb import (
    TMDB_API_KEY,
    TMDB_BASE_URL,
    get_popular_movies,
    get_movie_details,
    get_serie_details,
)
from scrapper import scrape_limetorrents, scrape_piratebay
import requests


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

ADDON_ID = "com.myaddonlocal.tmdb-addon"

# ----------------------------
# 1. MANIFEST
# ----------------------------
@app.get("/manifest.json")
def manifest():
    return {
        "id": ADDON_ID,
        "version": "1.0.0",
        "name": "My Addon",
        "description": "Movies & Series, animes",
        "resources": ["catalog", "meta", "stream"],
        "types": ["movie", "series"],
        "catalogs": [
            {
                "type": "movie",
                "id": "tmdb-popular"
            }
        ]
    }

# ----------------------------
# 2. CATALOG (Home page list)
# ----------------------------
@app.get("/catalog/movie/tmdb-popular.json")
def catalog():
    movies = get_popular_movies()

    metas = []
    for m in movies:
        metas.append({
            "id": f"tmdb:{m['id']}",
            "type": "movie",
            "name": m["title"],
            "poster": f"https://image.tmdb.org/t/p/w500{m['poster_path']}" if m.get("poster_path") else None,
            "description": m.get("overview", "")
        })

    return {"metas": metas}

# ----------------------------
# 3. META (details page)
# ----------------------------
@app.get("/meta/movie/{id}.json")
def meta(id: str):
    tmdb_id = id.replace("tmdb:", "")
    data = get_movie_details(tmdb_id)

    return {
        "meta": {
            "id": id,
            "type": "movie",
            "name": data.get("title"),
            "description": data.get("overview"),
            "poster": f"https://image.tmdb.org/t/p/w500{data.get('poster_path')}",
            "background": f"https://image.tmdb.org/t/p/w780{data.get('backdrop_path')}"
        }
    }

@app.get("/meta/{type}/{id}.json")
def meta(type:str, id: str):
    return get_serie_details(type, id)

# ----------------------------
# 4. STREAM (PLAY LINKS)
# ----------------------------
@app.get("/stream/movie/{id}.json")
def stream(id: str):
    # Cache the TMDB resolution so we only hit it once even if both scrapers fall back.
    _cached_title = {}

    def title_year():
        if "v" not in _cached_title:
            _cached_title["v"] = _imdb_to_title_year(id)
        return _cached_title["v"]

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_pb = ex.submit(_scrape_with_fallback, scrape_piratebay, id, title_year)
        f_lt = ex.submit(_scrape_with_fallback, scrape_limetorrents, id, title_year)
        pb_streams = f_pb.result()
        lt_streams = f_lt.result()

    seen = {}
    for s in (*pb_streams, *lt_streams):
        h = (s.get("infoHash") or "").lower()
        if not h:
            continue
        # On duplicates, keep the entry with the higher seed count.
        if h not in seen or s.get("_seeders", 0) > seen[h].get("_seeders", 0):
            seen[h] = s

    merged = sorted(seen.values(), key=lambda s: s.get("_seeders", 0), reverse=True)
    for s in merged:
        s.pop("_seeders", None)

    return {"streams": merged}