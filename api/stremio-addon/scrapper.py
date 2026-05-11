import requests
from bs4 import BeautifulSoup

from routers.tvdb import get_episode_counts, get_tvdb_id, get_all_english_names
from routers.tmdb import get_serie_details

def _get_soup(url):
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    res = requests.get(url, headers=headers)
    return BeautifulSoup(res.text, "html.parser")


def get_1337x_streams(query: str):
    def _search_torrents(domain, url):
        soup = _get_soup(url)

        results = []

        table = soup.select(".table-list-wrap tr")

        for idx, row in enumerate(table):
            if idx == 0:
                continue
            title = row.select_one(".name").text.strip()
            magnet_href = row.select(".name a")[-1].get("href")
            seeders = int(row.select_one(".seeds").text.strip())
            size = row.select_one(".size").text.strip()
            results.append({
                "title": title,
                "infoHash": f"https://www.{domain}{magnet_href}",
                "seeders": seeders,
                "size": size,
            })

        print(len(results))
        return sorted(results, key=lambda x: x["seeders"], reverse=True)
    
    def _scrape_detail(url):
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(res.text, "html.parser")

        # example: extract torrent magnet or info
        magnet = soup.select_one("a.torrentdown1").get("href").strip()
        return magnet.split("&")[0].split(":")[-1].strip()

    data : list[dict] = []
    domains = [
        "1337x.tw",
        "1337x.to",
        "1377x.to",
        "1337xx.to"
    ]

    while not data and domains:
        domain = domains.pop(0)
        url = f"https://www.{domain}/sort-category-search/{query}/Movies/seeders/desc/1/"
        try:
            data = _search_torrents(domain, url)
        except:
            continue
    
    for obj in data:
        magnet_link = obj.get("infoHash")
        obj["infoHash"] = _scrape_detail(magnet_link)
    print(len(data))
    return data


def get_thepiratebay_streams(movie_info):
    def _search_torrents(url):
            soup = _get_soup(url)

            results = []

            table = soup.select("#searchResult tr")[1:]

            for row in table:
                tds = row.find_all("td")
                if len(tds) < 8:
                    continue
                title = tds[1].text.strip()
                magnet_href = tds[3].select_one("a").get("href").strip()
                seeders = int(tds[5].text.strip())
                size = tds[4].text.strip()
                publisher = tds[7].text.strip()
                results.append({
                    "title": f"[{publisher}] - {title}",
                    "infoHash": magnet_href.split("&")[0].split(":")[-1],
                    "seeders": seeders,
                    "size": size,
                })

            return sorted(results, key=lambda x: x["seeders"], reverse=True)
    
    title = movie_info.get("title")
    release_year = movie_info.get("release_date").split("-")[0]
    domains = [
        "https://thepiratebay.bond/",
        "https://thepiratebay11.com/",
        "https://thepiratebay10.info/",
        "https://thepiratebay7.com/",
        "https://thepiratebay0.org/",
        "https://thepiratebay10.xyz/"
    ]
    data: list[dict] = []
    while not data and domains:
        domain = domains.pop(0)
        url = f"{domain}search/{title}%20{release_year}/1/99/207"
        try:
            data = _search_torrents(url)
        except Exception as e:
            print(e)
            continue
    
    return data


def get_nyaa_streams(show_detail, season, episode):
    def _search_torrents(url):
            soup = _get_soup(url)
            results = []

            table = soup.select(".table-responsive table tbody tr")

            for row in table:
                tds = row.find_all("td")
                if len(tds) < 7:
                    continue
                title = tds[1].text.strip()
                magnet_href = tds[2].select("a")[-1].get("href").strip()
                seeders = int(tds[5].text.strip())
                size = tds[3].text.strip()
                results.append({
                    "title": f"{title}",
                    "infoHash": magnet_href.split("&")[0].split(":")[-1],
                    "seeders": seeders,
                    "size": size,
                })

            return sorted(results, key=lambda x: x["seeders"], reverse=True)

    name = show_detail.get("meta").get("name").replace(" ", "+")
    url = f"https://nyaa.si/?f=0&c=1_0&q={name}+s{str(season).zfill(2)}e{str(episode).zfill(2)}&s=seeders&o=desc"
    streams = _search_torrents(url)
    return streams


def get_nyaa_streams_vv(imdb_id, season, episode):
    def _search_torrents(url):
        results = []
        p = 0
        while True:
            p += 1
            soup = _get_soup(url + f"&p={p}")
            table = soup.select(".table-responsive table tbody tr")

            for row in table:
                tds = row.find_all("td")
                if len(tds) < 7:
                    continue
                title = tds[1].text.strip()
                magnet_href = tds[2].select("a")[-1].get("href").strip()
                seeders = int(tds[5].text.strip())
                size = tds[3].text.strip()
                results.append({
                    "title": f"{title}",
                    "infoHash": magnet_href.split("&")[0].split(":")[-1],
                    "seeders": seeders,
                    "size": size,
                })
            
            next = soup.select_one("ul.pagination li.next a")
            if next is None or next.has_attr("href") is False:
                break

        return sorted(results, key=lambda x: x["seeders"], reverse=True)

    tvdb_id = get_tvdb_id(imdb_id)
    seasons = get_episode_counts(tvdb_id)

    absolute_episode = 0
    for item in seasons:
        if int(item.get("season_number")) == int(season):
            break
        absolute_episode += int(item.get("episode_count"))
    absolute_episode += int(episode)
    print(absolute_episode)
    show_detail = get_serie_details("series", imdb_id).get("meta")
    first_name = show_detail.get("name").replace(" ", "+")
    streams = []
    
    names = sorted([first_name] + get_all_english_names(tvdb_id))
    while names and len(streams) < 5:
        name = names.pop(0)
        url = f"https://nyaa.si/?f=0&c=1_0&q=%5BSubsPlease%5D+{name.replace(' ', '+')}+{str(absolute_episode).zfill(2)}&s=seeders&o=desc"
        print(url)
        streams += _search_torrents(url)
    
    url = f"https://nyaa.si/?f=0&c=1_0&q={first_name}+s{str(season).zfill(2)}e{str(episode).zfill(2)}&s=seeders&o=desc"
    streams = _search_torrents(url)
    return streams