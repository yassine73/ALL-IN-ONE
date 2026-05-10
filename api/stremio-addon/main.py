from fastapi import FastAPI
from tmdb import get_popular_movies, get_movie_details, get_serie_details
from scrapper import get_1337x_streams, get_thepiratebay_streams, get_nyaa_streams

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
    tmdb_id = id.replace("tmdb:", "")
    data = get_movie_details(tmdb_id)

    # ⚠️ Placeholder stream (you will replace later with real sources)
    streams = {
        "streams": get_thepiratebay_streams(data)
    }
    return streams

@app.get("/stream/{type}/{id}:{season}:{episode}.json")
def stream_serie(type: str, id: str, season: str, episode: str):
    show_detail = get_serie_details(type, id)

    # ⚠️ Placeholder stream (you will replace later with real sources)
    streams = {
        "streams": get_nyaa_streams(show_detail, season, episode)
    }
    return streams