import requests
from bs4 import BeautifulSoup

def get_1337x_streams(query: str):
    def _search_torrents(url):
        headers = {
            "User-Agent": "Mozilla/5.0"
        }

        res = requests.get(url, headers=headers)
        soup = BeautifulSoup(res.text, "html.parser")

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
    return data


def get_thepiratebay_streams(movie_info):
    def _search_torrents(url):
            headers = {
                "User-Agent": "Mozilla/5.0"
            }

            res = requests.get(url, headers=headers)
            soup = BeautifulSoup(res.text, "html.parser")

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