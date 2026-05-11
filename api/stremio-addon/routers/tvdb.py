import requests, os
from collections import defaultdict
from dotenv import load_dotenv
load_dotenv()

def _get_token():
    r = requests.post(
        f"https://api4.thetvdb.com/v4/login",
        json={"apikey": os.getenv("TVDB_API_KEY")}
    )
    return r.json()["data"]["token"]

def get_episode_counts(series_id):
    BASE_URL = "https://api4.thetvdb.com/v4"

    token = _get_token()
    headers = {
        "Authorization": f"Bearer {token}"
    }

    counts = defaultdict(int)

    page = 0

    while True:
        r = requests.get(
            f"{BASE_URL}/series/{series_id}/episodes/official/eng",
            headers=headers,
            params={"page": page}
        )

        data = r.json()["data"]

        episodes = data.get("episodes", [])

        if not episodes:
            break

        for ep in episodes:
            season = ep.get("seasonNumber")

            # ignore specials
            if season in [0, None]:
                continue

            counts[season] += 1

        links = data.get("links", {})

        if links.get("next") is None:
            break

        page += 1

    result = []

    for season in sorted(counts.keys()):
        result.append({
            "season_number": season,
            "episode_count": counts[season],
            "name": f"Season {season}"
        })

    return result

def get_tvdb_id(imdb_id):
    base_url = "https://api.themoviedb.org/3"
    
    # 1. Find the TMDB entry using the IMDb ID
    find_url = f"{base_url}/find/{imdb_id}"
    params = {
        "api_key": os.getenv("TMDB_API_KEY"),
        "external_source": "imdb_id"
    }
    
    response = requests.get(find_url, params=params)
    data = response.json()
    
    # Extract the TMDB ID (check tv_results for anime/shows)
    if not data.get('tv_results'):
        return "Show not found on TMDB."
    
    tmdb_id = data['tv_results'][0]['id']

    # 2. Get External IDs for that TMDB ID
    ext_url = f"{base_url}/tv/{tmdb_id}/external_ids"
    ext_params = {"api_key": os.getenv("TMDB_API_KEY")}
    
    ext_response = requests.get(ext_url, params=ext_params)
    return ext_response.json().get("tvdb_id")

def get_all_english_names(tvdb_id):
    token = _get_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept-Language": "eng"
    }

    r = requests.get(
        f"https://api4.thetvdb.com/v4/series/{tvdb_id}/extended",
        headers=headers
    )

    data = r.json()["data"]

    names = set()

    # Main English title
    if data.get("name"):
        names.add(data["name"])

    # Aliases
    for alias in data.get("aliases", []):
        if isinstance(alias, dict):
            name = alias.get("name")
        else:
            name = alias

        if name:
            names.add(name)

    return sorted(names)