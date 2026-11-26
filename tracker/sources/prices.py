"""Daily prices, volume and splits from Yahoo Finance via yfinance (free, no key)."""
import logging

from .. import config

log = logging.getLogger(__name__)


def fetch(conn, tickers, period=None):
    if config.OFFLINE:
        from . import synthetic
        return synthetic.prices(conn, tickers)
    import yfinance as yf

    ok = failed = 0
    for t in tickers:
        have = conn.execute("SELECT COUNT(*) FROM prices WHERE ticker=?", (t,)).fetchone()[0]
        p = period or ("1mo" if have > 200 else "3y")
        try:
            tk = yf.Ticker(t)
            df = tk.history(period=p, auto_adjust=False, actions=True)
        except Exception as e:  # network or delisted
            log.warning("prices %s failed: %s", t, e)
            failed += 1
            continue
        if df is None or df.empty:
            log.warning("no price data for %s (delisted or renamed?)", t)
            failed += 1
            continue
        rows = [(t, idx.strftime("%Y-%m-%d"), float(r.Open), float(r.High), float(r.Low),
                 float(r.Close), float(r.Volume)) for idx, r in df.iterrows()]
        conn.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)", rows)
        if "Stock Splits" in df:
            splits = df[df["Stock Splits"] > 0]["Stock Splits"]
            conn.executemany("INSERT OR REPLACE INTO splits VALUES (?,?,?)",
                             [(t, i.strftime("%Y-%m-%d"), float(v)) for i, v in splits.items()])
        conn.commit()
        log.info("prices %s: %d rows", t, len(rows))
        ok += 1
    return ok, failed


def short_interest(conn, tickers, today):
    """Snapshot of short % of float from Yahoo (which republishes FINRA's twice-monthly data)."""
    if config.OFFLINE:
        return
    import yfinance as yf
    for t in tickers:
        try:
            info = yf.Ticker(t).info
        except Exception as e:
            log.warning("short interest %s failed: %s", t, e)
            continue
        pct = info.get("shortPercentOfFloat")
        dtc = info.get("shortRatio")
        if pct is not None:
            conn.execute("INSERT OR REPLACE INTO short_interest VALUES (?,?,?,?)",
                         (t, today, float(pct), float(dtc) if dtc else None))
    conn.commit()
