"""One-shot tracker latency benchmark.

Probes each UDP tracker with a BEP-15 connect handshake, takes the median of
N samples, and prints the list sorted fastest-first. Run this occasionally
and paste the top entries into scrapper.py's _TRACKERS.
"""
import os
import random
import socket
import statistics
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

CANDIDATES = """
udp://tracker.opentrackr.org:1337/announce
udp://open.demonii.com:1337/announce
udp://open.stealth.si:80/announce
udp://tracker.torrent.eu.org:451/announce
udp://vito-tracker.space:6969/announce
udp://vito-tracker.duckdns.org:6969/announce
udp://udp.tracker.projectk.org:23333/announce
udp://tracker.tryhackx.org:6969/announce
udp://tracker.t-1.org:6969/announce
udp://tracker.startwork.cv:1337/announce
udp://tracker.srv00.com:6969/announce
udp://tracker.qu.ax:6969/announce
udp://tracker.plx.im:6969/announce
udp://tracker.opentorrent.top:6969/announce
udp://tracker.iperson.xyz:6969/announce
udp://tracker.gmi.gd:6969/announce
udp://tracker.ducks.party:1984/announce
udp://tracker.bluefrog.pw:2710/announce
udp://tracker.bittor.pw:1337/announce
udp://tracker.auctor.tv:6969/announce
udp://tracker.openbittorrent.com:80/announce
udp://exodus.desync.com:6969/announce
udp://open.tracker.cl:1337/announce
udp://explodie.org:6969/announce
""".strip().splitlines()

SAMPLES = 3
TIMEOUT = 3.0
MAGIC = 0x41727101980


def _probe_once(host: str, port: int) -> float | None:
    tid = random.randint(0, 0xFFFFFFFF)
    pkt = struct.pack("!QII", MAGIC, 0, tid)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(TIMEOUT)
    try:
        s.connect((host, port))
        t0 = time.perf_counter()
        s.send(pkt)
        data = s.recv(64)
        dt = (time.perf_counter() - t0) * 1000.0
    except Exception:
        return None
    finally:
        s.close()
    if len(data) < 16:
        return None
    action, rtid = struct.unpack("!II", data[:8])
    if action != 0 or rtid != tid:
        return None
    return dt


def bench(url: str):
    p = urlparse(url)
    try:
        # Resolve once; pick the first A record.
        addrs = socket.getaddrinfo(p.hostname, p.port, type=socket.SOCK_DGRAM)
        host, port = addrs[0][4][0], p.port
    except Exception as e:
        return url, None, f"dns: {e}"
    samples = []
    for _ in range(SAMPLES):
        rtt = _probe_once(host, port)
        if rtt is not None:
            samples.append(rtt)
    if not samples:
        return url, None, "no reply"
    return url, statistics.median(samples), f"{len(samples)}/{SAMPLES}"


def main():
    with ThreadPoolExecutor(max_workers=min(16, len(CANDIDATES))) as ex:
        rows = list(ex.map(bench, CANDIDATES))
    alive = [(u, ms, n) for (u, ms, n) in rows if ms is not None]
    dead = [(u, _, n) for (u, _, n) in rows if _ is None]
    alive.sort(key=lambda r: r[1])
    print(f"ALIVE ({len(alive)}):")
    for u, ms, n in alive:
        print(f"  {ms:7.1f} ms  [{n}]  {u}")
    print(f"\nDEAD ({len(dead)}):")
    for u, _, n in dead:
        print(f"  --       [{n}]    {u}")


if __name__ == "__main__":
    main()
