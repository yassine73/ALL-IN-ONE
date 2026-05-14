import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import re

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

def extract_infohash(magnet: str):
    match = re.search(r"btih:([a-fA-F0-9]+)", magnet)
    return match.group(1) if match else None

def scrape_piratebay(query: str):
    url = f"https://thepiratebay.org/search/{query}/0/99/0"

    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(url, timeout=60000)

        # Wait for JS to render results
        page.wait_for_timeout(5000)

        # Try waiting for table rows (dynamic fallback)
        page.wait_for_selector("li.list-entry", timeout=15000)

        rows = page.query_selector_all("li.list-entry")

        for row in rows:
            try:
                cols = row.query_selector_all("span")
                if len(cols) < 8:
                    continue

                # TITLE + MAGNET
                title_el = cols[1].query_selector("a")
                if not title_el:
                    continue

                title = title_el.inner_text().strip()

                magnet_el = row.query_selector("a[href^='magnet:']")
                magnet = magnet_el.get_attribute("href") if magnet_el else None
                infohash = extract_infohash(magnet) if magnet else None

                # SEEDERS / SIZE
                seeders = cols[5].inner_text().strip()
                size = cols[4].inner_text().strip() if len(cols) > 4 else None
                uled_by = cols[7].inner_text().strip()

                # Skip invalid rows
                if not title or not infohash:
                    continue

                results.append({
                    "title": f"[{uled_by}] - {title}",
                    "infoHash": infohash,
                    "seeders": int(seeders) if seeders.isdigit() else 0,
                    "size": size
                })

            except Exception:
                continue

        browser.close()

    return results
