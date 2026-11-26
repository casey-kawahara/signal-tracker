"""FINRA daily short-sale volume files (free, no key).

Each trading day FINRA publishes how many shares were sold short on its venues.
A jump in the short share of volume can mean traders are betting against a run-up,
or set up a squeeze if the price keeps rising.
"""
import logging
from datetime import date, timedelta

import requests

from .. import config

log = logging.getLogger(__name__)
URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d}.txt"


def fetch(conn, tickers, days_back=10):
    if config.OFFLINE:
        from . import synthetic
        return synthetic.short_volume(conn, tickers)
    want = set(tickers)
    d = date.today()
    for _ in range(days_back):
        d -= timedelta(days=1)
        if d.weekday() >= 5:
            continue
        ds = d.isoformat()
        if conn.execute("SELECT 1 FROM short_volume WHERE date=? LIMIT 1", (ds,)).fetchone():
            continue
        try:
            r = requests.get(URL.format(d=d.strftime("%Y%m%d")), timeout=30)
            if r.status_code == 404 or r.status_code == 403:
                continue  # holiday or not yet published
            r.raise_for_status()
        except Exception as e:
            log.warning("FINRA %s failed: %s", ds, e)
            continue
        rows = []
        for line in r.text.splitlines()[1:]:
            parts = line.split("|")
            if len(parts) >= 5 and parts[1] in want:
                rows.append((parts[1], ds, float(parts[2]), float(parts[4])))
        conn.executemany("INSERT OR REPLACE INTO short_volume VALUES (?,?,?,?)", rows)
        conn.commit()
