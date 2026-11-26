"""Google Trends search interest via pytrends (free, unofficial, sometimes rate-limited)."""
import logging
import time

from .. import config

log = logging.getLogger(__name__)


def fetch(conn, tickers, timeframe="today 3-m"):
    if config.OFFLINE:
        from . import synthetic
        return synthetic.trends(conn, tickers)
    try:
        from pytrends.request import TrendReq
        py = TrendReq(hl="en-US", tz=300, timeout=(10, 30))
    except Exception as e:
        log.warning("pytrends unavailable: %s", e)
        return 0, len(tickers)
    ok = failed = 0
    for t in tickers:
        kw = f"{t} stock"
        try:
            py.build_payload([kw], timeframe=timeframe, geo="US")
            df = py.interest_over_time()
        except Exception as e:  # 429s are common from cloud IPs
            log.warning("trends %s failed: %s", t, e)
            failed += 1
            time.sleep(5)
            continue
        if df is None or df.empty:
            failed += 1
            continue
        conn.executemany("INSERT OR REPLACE INTO trends_daily VALUES (?,?,?)",
                         [(t, i.strftime("%Y-%m-%d"), float(v)) for i, v in df[kw].items()])
        conn.commit()
        ok += 1
        time.sleep(2)
    return ok, failed
