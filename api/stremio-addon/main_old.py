from fastapi import FastAPI
from fastapi.responses import RedirectResponse
import requests

app = FastAPI()

# ----------------------------
# CONFIG
# ----------------------------
TMDB_API_KEY = "8d9b331b67d750f22f604ce71a5eff37"
TMDB_BASE = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/w500"

ACTIVE_STREAMS = {}


# ----------------------------
# MANIFEST
# ----------------------------
@app.get("/manifest.json")
def manifest():
    return {
        "id": "com.tmdb.webtorrent",
        "version": "1.0.0",
        "name": "TMDB WebTorrent Addon",
        "description": "TMDB catalog + torrent streaming",
        "resources": ["catalog", "meta", "stream"],
        "types": ["movie"],
        "catalogs": [
            {
                "type": "movie",
                "id": "tmdb-popular",
                "name": "TMDB Popular"
            }
        ]
    }


# ----------------------------
# TMDB CATALOG (5000+ movies possible)
# ----------------------------
@app.get("/catalog/movie/tmdb-popular.json")
def catalog():

    movies = []

    for page in range(1, 6):  # increase to 100+ for large catalog
        res = requests.get(
            f"{TMDB_BASE}/movie/popular",
            params={
                "api_key": TMDB_API_KEY,
                "page": page
            }
        ).json()

        for m in res.get("results", []):
            movies.append({
                "id": str(m["id"]),
                "type": "movie",
                "name": m["title"],
                "poster": IMG + m["poster_path"] if m.get("poster_path") else None,
                "description": m.get("overview", "")
            })

    return {"metas": movies}


# ----------------------------
# META (TMDB details)
# ----------------------------
@app.get("/meta/movie/{movie_id}.json")
def meta(movie_id: str):

    res = requests.get(
        f"{TMDB_BASE}/movie/{movie_id}",
        params={"api_key": TMDB_API_KEY}
    ).json()

    return {
        "meta": {
            "id": str(res["id"]),
            "type": "movie",
            "name": res["title"],
            "poster": IMG + res["poster_path"] if res.get("poster_path") else None,
            "description": res.get("overview", ""),
            "genres": [g["name"] for g in res.get("genres", [])]
        }
    }


# ----------------------------
# TORRENT SEARCH (YTS)
# ----------------------------
def search_torrents(query):
    try:
        url = f"https://yts.mx/api/v2/list_movies.json?query_term={query}"
        res = requests.get(url, timeout=5).json()
    except (requests.exceptions.RequestException, ValueError):
        print("Error fetching torrents from YTS")
        return []

    torrents = []
    try:
        for movie in res.get("data", {}).get("movies", []):
            for t in movie.get("torrents", []):
                torrents.append({
                    "title": f"{movie['title']} {t['quality']}",
                    "magnet": f"magnet:?xt=urn:btih:{t['hash']}",
                    "quality": t["quality"],
                    "seeds": t.get("seeds", 0)
                })
    except (AttributeError, TypeError):
        return []

    return sorted(torrents, key=lambda x: x["seeds"], reverse=True)


# ----------------------------
# STREAM (Stremio entry point)
# ----------------------------
@app.get("/stream/movie/{movie_id}.json")
def stream(movie_id: str):

    # 1. Get movie title from TMDB to search YTS
    res = requests.get(
        f"{TMDB_BASE}/movie/{movie_id}",
        params={"api_key": TMDB_API_KEY}
    ).json()

    title = res.get("title")
    if not title:
        return {"streams": []}

    # 2. Search torrents using the movie title
    torrents = search_torrents(title)

    if not torrents:
        return {"streams": []}

    # 3. Return magnet links directly. 
    # Stremio handles magnet links natively, so there is no need to 
    # manage a local webtorrent process and proxy the stream.
    streams = []
    for t in torrents:
        streams.append({
            "title": f"🔥 {t['quality']} ({t['seeds']} seeds)",
            "url": t["magnet"]
        })

    return {"streams": streams}


# The start_webtorrent and play endpoints were removed as Stremio 
# handles magnets natively and the local process approach is unstable.
