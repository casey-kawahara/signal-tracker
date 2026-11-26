"""Fake but plausible data for offline demos and tests (TRACKER_OFFLINE=1)."""
import math
import random
from datetime import date, timedelta


def _bdays(end, n):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def _end():
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def prices(conn, tickers, n=750):
    days = _bdays(_end(), n)
    for t in tickers:
        rng = random.Random(t)
        px = rng.uniform(0.5, 8.0) if t != "SPY" else 450.0
        vol_base = rng.uniform(2e6, 2e7) if t != "SPY" else 7e7
        pump_left = 0
        rows = []
        for d in days:
            sigma = 0.01 if t == "SPY" else 0.045
            drift = 0.0004 if t == "SPY" else -0.0008
            r = rng.gauss(drift, sigma)
            v = vol_base * math.exp(rng.gauss(0, 0.35))
            if t != "SPY" and pump_left == 0 and rng.random() < 0.01:
                pump_left = rng.randint(6, 12)
            if pump_left:
                # run up for a few days, then fade hard
                r += 0.12 if pump_left > 4 else -0.10
                v *= 4
                pump_left -= 1
            o = px
            px = max(0.05, px * math.exp(r))
            rows.append((t, d.isoformat(), o, max(o, px) * 1.01, min(o, px) * 0.99, px, v))
        conn.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()


def filings(conn, tickers):
    days = _bdays(_end(), 750)
    for t in tickers:
        rng = random.Random("f" + t)
        for i, d in enumerate(days):
            x = rng.random()
            form, items, buy, sell = None, "", 0, 0
            if x < 0.02:
                form, items = "8-K", rng.choice(["7.01", "8.01", "2.02", "3.02", "5.03"])
            elif x < 0.025:
                form = rng.choice(["S-3", "424B5"])
            elif x < 0.035:
                form = "4"
                if rng.random() < 0.3:
                    buy = rng.randint(10_000, 500_000)
                else:
                    sell = rng.randint(10_000, 500_000)
            if form:
                conn.execute("INSERT OR IGNORE INTO filings VALUES (?,?,?,?,?,?,?)",
                             (f"SYN-{t}-{i}", t, form, d.isoformat(), items, buy, sell))
    conn.commit()


def reddit(conn, tickers, today):
    for t in tickers:
        rng = random.Random(t + today)
        n = int(rng.expovariate(1 / 6))
        conn.execute("INSERT OR REPLACE INTO reddit_daily VALUES (?,?,?,?,?)",
                     (t, today, n, n * rng.randint(1, 40), rng.uniform(-0.5, 0.7)))
    conn.commit()


def trends(conn, tickers):
    days = _bdays(_end(), 90)
    for t in tickers:
        rng = random.Random("t" + t)
        conn.executemany("INSERT OR REPLACE INTO trends_daily VALUES (?,?,?)",
                         [(t, d.isoformat(), max(0, rng.gauss(30, 12))) for d in days])
    conn.commit()


def short_volume(conn, tickers):
    days = _bdays(_end(), 30)
    for t in tickers:
        rng = random.Random("s" + t)
        conn.executemany("INSERT OR REPLACE INTO short_volume VALUES (?,?,?,?)",
                         [(t, d.isoformat(), rng.uniform(2e5, 2e6), 4e6) for d in days])
    conn.commit()


def scan(conn, as_of):
    """Fake scan: score every synthetic ticker by yesterday's volume spike and move."""
    from .. import config, scanner
    if not conn.execute("SELECT 1 FROM prices WHERE ticker=? LIMIT 1", (config.REPLAY_POOL[-1],)).fetchone():
        prices(conn, config.REPLAY_POOL)
    out = []
    pool = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM prices WHERE ticker != 'SPY'")]
    for t in pool:
        rows = conn.execute("SELECT close, volume FROM prices WHERE ticker=? AND date<=? "
                            "ORDER BY date DESC LIMIT 21", (t, as_of)).fetchall()
        if len(rows) < 21:
            continue
        avg = sum(r[1] for r in rows[1:]) / 20
        rng = random.Random(t + as_of)
        m0 = int(rng.expovariate(1 / 8))
        m = m0 * rng.choice([1, 1, 1, 2, 5]) + rng.randint(0, 5)
        c = {"ticker": t, "price": rows[0][0], "volume": rows[0][1], "avg_volume": avg,
             "vol_ratio": rows[0][1] / avg, "change": rows[0][0] / rows[1][0] - 1,
             "mentions": m, "mentions_prev": m0}
        c["score"] = scanner.score(c)
        conn.execute("INSERT OR REPLACE INTO buzz VALUES (?,?,?,?,?,?)", (t, as_of, m, m0, 0, 0))
        out.append(c)
    conn.commit()
    out.sort(key=lambda c: -c["score"])
    return out
