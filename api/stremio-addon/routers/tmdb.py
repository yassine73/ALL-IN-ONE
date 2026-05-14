import requests, os

from dotenv import load_dotenv
load_dotenv()

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

def get_popular_movies():
    url = f"{TMDB_BASE_URL}/movie/popular"
    params = {"api_key": TMDB_API_KEY}
    r = requests.get(url, params=params)
    return r.json().get("results", [])

def get_movie_details(movie_id):
    url = f"{TMDB_BASE_URL}/movie/{movie_id}"
    params = {"api_key": TMDB_API_KEY}
    r = requests.get(url, params=params)
    return r.json()

def imdb_tmdb_converter(imdb_id):
    url = f"{TMDB_BASE_URL}/find/{imdb_id}"

    params = {
        "api_key": TMDB_API_KEY,
        "external_source": "imdb_id"
    }

    r = requests.get(url, params=params)
    data = r.json()

    # TV results
    tv_results = data.get("tv_results", [])

    if not tv_results:
        return None

    return tv_results[0]["id"]

def get_imdb_serie(imdb_id):
    # Step 1: convert IMDb → TMDb
    tmdb_id = imdb_tmdb_converter(imdb_id)

    # Step 2: fetch full TV details
    url = f"{TMDB_BASE_URL}/tv/{tmdb_id}"
    r = requests.get(url, params={"api_key": TMDB_API_KEY})

    return r.json()

def get_serie_details(type: str, imdb_id: str):
    if type != "series":
        return None

    serie = get_imdb_serie(imdb_id)

    return {
        "meta": {
            "id": imdb_id,
            "type": "series",
            "name": serie.get("name") or serie.get("original_name"),
            "description": serie.get("overview"),
            "poster": (
                f"https://image.tmdb.org/t/p/w500{serie['poster_path']}"
                if serie.get("poster_path") else None
            ),
            "background": (
                f"https://image.tmdb.org/t/p/original{serie['backdrop_path']}"
                if serie.get("backdrop_path") else None
            ),
            "releaseInfo": serie.get("first_air_date"),
            "imdbRating": serie.get("vote_average"),
            "genres": [g["name"] for g in serie.get("genres", [])],
            "runtime": None,
            "videos": []
        }
    }