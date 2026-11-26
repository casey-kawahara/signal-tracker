"""Reddit mention counts and a simple sentiment score (free API).

With REDDIT_CLIENT_ID/SECRET set, uses the official OAuth API (reliable).
Without them, falls back to the public JSON endpoints, which Reddit often blocks
from cloud servers. Reddit only returns recent posts, so this signal builds up
from the day the tracker starts; it cannot be backtested.
"""
import logging
import re
import time
from datetime import datetime, timezone

import requests

from .. import config

log = logging.getLogger(__name__)

BULL = {"moon", "rocket", "squeeze", "buy", "buying", "calls", "bullish", "breakout", "long",
        "undervalued", "rip", "runner", "gap", "🚀", "💎", "yolo", "load", "loading"}
BEAR = {"dump", "dumping", "puts", "short", "bearish", "dilution", "offering", "scam", "bagholder",
        "bagholding", "sell", "selling", "crash", "rug", "reverse", "split", "overvalued", "bankrupt"}
UA = "signal-tracker/0.1 (personal research script)"


def sentiment(text):
    words = re.findall(r"[\w']+|[🚀💎]", text.lower())
    b = sum(w in BULL for w in words)
    s = sum(w in BEAR for w in words)
    return 0.0 if b + s == 0 else (b - s) / (b + s)


def _session():
    s = requests.Session()
    s.headers["User-Agent"] = UA
    if config.REDDIT_CLIENT_ID and config.REDDIT_CLIENT_SECRET:
        r = s.post("https://www.reddit.com/api/v1/access_token",
                   auth=(config.REDDIT_CLIENT_ID, config.REDDIT_CLIENT_SECRET),
                   data={"grant_type": "client_credentials"}, timeout=30)
        r.raise_for_status()
        s.headers["Authorization"] = "bearer " + r.json()["access_token"]
        return s, "https://oauth.reddit.com"
    return s, "https://www.reddit.com"


def _mentions_text(text, ticker):
    return re.search(rf"(\${ticker}\b|\b{ticker}\b)", text) is not None


def fetch(conn, tickers, today):
    if config.OFFLINE:
        from . import synthetic
        return synthetic.reddit(conn, tickers, today)
    try:
        s, base = _session()
    except Exception as e:
        log.warning("reddit auth failed: %s", e)
        return
    cutoff = datetime.now(timezone.utc).timestamp() - 86400
    subs = "+".join(config.REDDIT_SUBS)
    for t in tickers:
        try:
            r = s.get(f"{base}/r/{subs}/search.json",
                      params={"q": f'"{t}" OR "${t}"', "restrict_sr": 1, "sort": "new",
                              "t": "day", "limit": 100}, timeout=30)
            r.raise_for_status()
            posts = [c["data"] for c in r.json()["data"]["children"]]
        except Exception as e:
            log.warning("reddit %s failed: %s", t, e)
            continue
        posts = [p for p in posts if p.get("created_utc", 0) >= cutoff
                 and _mentions_text(p.get("title", "") + " " + p.get("selftext", ""), t)]
        score = sum(int(p.get("score", 0)) for p in posts)
        sents = [sentiment(p.get("title", "") + " " + p.get("selftext", "")) for p in posts]
        avg = sum(sents) / len(sents) if sents else 0.0
        conn.execute("INSERT OR REPLACE INTO reddit_daily VALUES (?,?,?,?,?)",
                     (t, today, len(posts), score, avg))
        time.sleep(1.2)
    conn.commit()
