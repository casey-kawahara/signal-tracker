"""Each morning, find the small and penny stocks getting the most unusual attention.

Candidates come from two free sources:
  - Yahoo Finance's stock screener: US small caps sorted by yesterday's trading volume
  - ApeWisdom (apewisdom.io): Reddit mention counts now vs 24 hours earlier

Each candidate gets an "attention score" from how unusual its volume, Reddit buzz and price
move are. The top picks are locked in the `selections` table before any prediction is made,
so every pick gets scored and the record can't be cherry-picked later.
"""
import json
import logging
import math
from datetime import datetime, timezone

import requests

from . import config, db

log = logging.getLogger(__name__)
APEWISDOM = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/{page}"


def score(c):
    """Attention score. Each part is 0 when nothing unusual is happening."""
    vol_ratio = c.get("vol_ratio") or 0
    m, m0 = c.get("mentions") or 0, c.get("mentions_prev") or 0
    move = abs(c.get("change") or 0)
    s = 0.0
    if vol_ratio > 1:
        s += math.log2(vol_ratio)                     # 2x volume = 1 point, 4x = 2, 8x = 3
    if m >= 5:
        s += 0.8 * max(0.0, math.log2((m + 3) / (m0 + 3)))  # mention jump
        s += 0.3 * min(math.log10(m), 2)              # sheer amount of chatter
    s += min(move / 0.10, 3) * 0.5                    # 10% move = 0.5 point, capped
    return round(s, 3)


def reasons(c):
    """Plain-English reasons a stock was picked, strongest first."""
    out = []
    vr = c.get("vol_ratio") or 0
    if vr >= 1.8:
        out.append((math.log2(vr), f"About {vr:.0f}× its usual trading volume" if vr >= 2.5
                    else "Much heavier trading than usual"))
    m, m0 = c.get("mentions") or 0, c.get("mentions_prev") or 0
    if m >= 5 and (m + 3) / (m0 + 3) >= 1.8:
        out.append((0.8 * math.log2((m + 3) / (m0 + 3)),
                    f"Reddit mentions jumped from {m0} to {m} in a day"))
    elif m >= 25:
        out.append((0.5, f"Talked about a lot on Reddit ({m} mentions in a day)"))
    ch = c.get("change")
    if ch is not None and abs(ch) >= 0.08:
        out.append((abs(ch) * 5, f"Price {'jumped' if ch > 0 else 'fell'} {abs(ch) * 100:.0f}% in one day"))
    out.sort(key=lambda x: -x[0])
    return [r for _, r in out] or ["One of the most actively traded small stocks yesterday"]


def _ok_symbol(sym):
    return sym and sym.isalpha() and len(sym) <= 5 and sym.upper() == sym


def yahoo_candidates(pages=2):
    import yfinance as yf
    from yfinance import EquityQuery as Q
    query = Q("and", [
        Q("eq", ["region", "us"]),
        Q("is-in", ["exchange", "NMS", "NYQ", "ASE", "NCM", "NGM"]),
        Q("btwn", ["intradaymarketcap", config.MIN_MARKET_CAP, config.MAX_MARKET_CAP]),
        Q("gt", ["intradayprice", config.MIN_PRICE]),
        Q("gt", ["dayvolume", config.MIN_VOLUME]),
    ])
    out = {}
    for page in range(pages):
        res = yf.screen(query, offset=page * 250, size=250, sortField="dayvolume", sortAsc=False)
        for q in res.get("quotes", []):
            sym = q.get("symbol")
            if not _ok_symbol(sym) or q.get("quoteType") not in (None, "EQUITY"):
                continue
            vol = q.get("regularMarketVolume") or 0
            avg = q.get("averageDailyVolume3Month") or q.get("averageDailyVolume10Day") or 0
            out[sym] = {
                "ticker": sym, "name": q.get("shortName") or q.get("longName") or "",
                "price": q.get("regularMarketPrice"), "market_cap": q.get("marketCap"),
                "volume": vol, "avg_volume": avg, "vol_ratio": vol / avg if avg else None,
                "change": (q.get("regularMarketChangePercent") or 0) / 100,
            }
        if len(res.get("quotes", [])) < 250:
            break
    return out


def apewisdom(conn, as_of, pages=3):
    """Reddit mention counts across stock subreddits. Also saved as daily buzz history."""
    rows = []
    for p in range(1, pages + 1):
        r = requests.get(APEWISDOM.format(page=p), timeout=30,
                         headers={"User-Agent": "signal-tracker/0.1"})
        r.raise_for_status()
        rows += r.json().get("results", [])
    conn.executemany(
        "INSERT OR REPLACE INTO buzz VALUES (?,?,?,?,?,?)",
        [(x["ticker"], as_of, int(x.get("mentions") or 0), int(x.get("mentions_24h_ago") or 0),
          int(x.get("upvotes") or 0), int(x.get("rank") or 0)) for x in rows if _ok_symbol(x.get("ticker"))])
    conn.commit()
    return {x["ticker"]: x for x in rows if _ok_symbol(x.get("ticker"))}


def _fill_quote(c):
    """Look up price, size and volume for a Reddit-only candidate."""
    import yfinance as yf
    try:
        fi = yf.Ticker(c["ticker"]).fast_info
        c["price"] = fi.get("lastPrice") or fi.get("last_price")
        c["market_cap"] = fi.get("marketCap") or fi.get("market_cap")
        vol = fi.get("lastVolume") or fi.get("last_volume") or 0
        avg = fi.get("threeMonthAverageVolume") or fi.get("three_month_average_volume") or 0
        prev = fi.get("previousClose") or fi.get("previous_close")
        c["volume"], c["avg_volume"] = vol, avg
        c["vol_ratio"] = vol / avg if avg else None
        if prev and c["price"]:
            c["change"] = c["price"] / prev - 1
    except Exception as e:
        log.debug("quote %s failed: %s", c["ticker"], e)
    return c


def eligible(c):
    return (c.get("price") or 0) >= config.MIN_PRICE and \
        config.MIN_MARKET_CAP <= (c.get("market_cap") or 0) <= config.MAX_MARKET_CAP and \
        max(c.get("volume") or 0, c.get("avg_volume") or 0) >= config.MIN_VOLUME


def scan(conn, as_of):
    """Return candidates ranked by attention score (best first)."""
    if config.OFFLINE:
        from .sources import synthetic
        return synthetic.scan(conn, as_of)
    cands = {}
    try:
        cands = yahoo_candidates()
        log.info("yahoo screener: %d candidates", len(cands))
        db.log_run(conn, "yahoo_screener", "ok" if cands else "error", f"{len(cands)} stocks")
    except Exception as e:
        log.warning("yahoo screener failed: %s", e)
        db.log_run(conn, "yahoo_screener", "error", repr(e))
    try:
        ape = apewisdom(conn, as_of)
        log.info("apewisdom: %d tickers", len(ape))
        db.log_run(conn, "reddit_mentions", "ok" if ape else "error", f"{len(ape)} stocks")
    except Exception as e:
        log.warning("apewisdom failed: %s", e)
        db.log_run(conn, "reddit_mentions", "error", repr(e))
        ape = {}
    for sym, x in ape.items():
        c = cands.get(sym)
        if c is None:
            if int(x.get("mentions") or 0) < 10:
                continue
            c = _fill_quote({"ticker": sym, "name": x.get("name", "")})
            cands[sym] = c
        c["mentions"] = int(x.get("mentions") or 0)
        c["mentions_prev"] = int(x.get("mentions_24h_ago") or 0)
    ranked = [c for c in cands.values() if eligible(c)]
    for c in ranked:
        c["score"] = score(c)
    ranked.sort(key=lambda c: -c["score"])
    return ranked


def lock_picks(conn, as_of, ranked, has_history, n=None):
    """Lock today's picks. `has_history(ticker)` says whether a prediction can be made."""
    n = n or config.PICKS_PER_DAY
    existing = conn.execute("SELECT ticker FROM selections WHERE as_of=? ORDER BY rank",
                            (as_of,)).fetchall()
    if existing:
        return [r[0] for r in existing]  # already picked for this trading day
    picks = []
    for c in ranked:
        if len(picks) >= n:
            break
        if has_history(c["ticker"]):
            picks.append({**c, "why": reasons(c)})
    if len(picks) < n:  # scan came up short: fill from the backup list, and say so
        for t in config.WATCHLIST:
            if len(picks) >= n:
                break
            if t not in {p["ticker"] for p in picks} and has_history(t):
                picks.append({"ticker": t, "score": 0.0,
                              "why": ["From the backup list (the scan didn't find enough stocks today)"]})
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for i, p in enumerate(picks, 1):
        detail = {k: p.get(k) for k in ("name", "price", "market_cap", "volume", "avg_volume",
                                         "vol_ratio", "change", "mentions", "mentions_prev")}
        conn.execute("INSERT INTO selections (created_at, as_of, ticker, rank, score, reasons, detail) "
                     "VALUES (?,?,?,?,?,?,?)",
                     (now, as_of, p["ticker"], i, p.get("score", 0), json.dumps(p["why"]),
                      json.dumps(detail, default=float)))
    conn.commit()
    log.info("locked %d picks for %s: %s", len(picks), as_of, ", ".join(p["ticker"] for p in picks))
    return [p["ticker"] for p in picks]
