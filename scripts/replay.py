"""Practice run: replay the last N trading days as if the tracker had run each morning.

Each replayed day it picks the stocks with the most unusual trading (volume spike and price
move) from a pool of small stocks, locks the picks, makes calls, and scores them later.
It works in a throwaway database, so the real record is never touched, and writes the page
to docs/replay/.

    python scripts/replay.py --fetch --days 30    # download fresh prices for the pool (needs internet)
    python scripts/replay.py --days 30            # reuse data/tracker.db
    TRACKER_OFFLINE=1 python scripts/replay.py    # made-up data, no internet

Caveats: there is no free history of Reddit mentions or the full stock universe, so replayed
picks come from a fixed pool of ~40 well-known small stocks and use trading activity only.
"""
import argparse
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import backtest, config, db, predict, report, scanner  # noqa: E402

RAW = ("filings", "trends_daily", "short_volume", "splits", "reddit_daily", "short_interest")


def _copy_all(src, dst, table):
    rows = src.execute(f"SELECT * FROM {table}").fetchall()
    if rows:
        dst.executemany(f"INSERT OR REPLACE INTO {table} VALUES ({','.join('?' * len(rows[0]))})",
                        [tuple(r) for r in rows])


def _candidates(sim, d, pool):
    """Score each pool stock by how unusual its trading was on day d (data known that night)."""
    out = []
    for t in pool:
        rows = sim.execute("SELECT close, volume FROM prices WHERE ticker=? AND date<=? "
                           "ORDER BY date DESC LIMIT 21", (t, d)).fetchall()
        if len(rows) < 21 or not rows[1][0]:
            continue
        avg = sum(r[1] for r in rows[1:]) / 20
        c = {"ticker": t, "price": rows[0][0], "volume": rows[0][1], "avg_volume": avg,
             "vol_ratio": rows[0][1] / avg if avg else None, "change": rows[0][0] / rows[1][0] - 1}
        bz = sim.execute("SELECT mentions, mentions_prev FROM buzz WHERE ticker=? AND date=?",
                         (t, d)).fetchone()
        if bz:
            c["mentions"], c["mentions_prev"] = bz
        c["score"] = scanner.score(c)
        out.append(c)
    return sorted(out, key=lambda c: -c["score"])


def replay(days=30, source=None, out_dir=None, note=None, fetch=False):
    tmp = Path(tempfile.mkdtemp())
    pool = config.REPLAY_POOL
    if source is None:
        from tracker.sources import finra, prices, sec, trends
        src = db.connect(tmp / "full.db")
        src.row_factory = sqlite3.Row
        prices.fetch(src, [config.MARKET_TICKER] + pool, period="2y")
        if config.OFFLINE or fetch:
            sec.fetch(src, pool)
        if config.OFFLINE:
            trends.fetch(src, pool)
            finra.fetch(src, pool)
    else:
        src = sqlite3.connect(source)
        src.row_factory = sqlite3.Row
    pool = [r[0] for r in src.execute("SELECT DISTINCT ticker FROM prices WHERE ticker != ?",
                                      (config.MARKET_TICKER,))]
    sim = db.connect(tmp / "sim.db")
    for t in RAW:
        _copy_all(src, sim, t)
    all_days = [r[0] for r in src.execute(
        "SELECT DISTINCT date FROM prices WHERE ticker=? ORDER BY date", (config.MARKET_TICKER,))]
    first_idx = len(all_days) - (days + 5)  # +5 so the last week-ahead calls can be scored
    rows = src.execute("SELECT * FROM prices WHERE date < ?", (all_days[first_idx],)).fetchall()
    sim.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)", [tuple(r) for r in rows])
    for d in all_days[first_idx:]:
        rows = src.execute("SELECT * FROM prices WHERE date = ?", (d,)).fetchall()
        sim.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)", [tuple(r) for r in rows])
        if config.OFFLINE:
            from tracker.sources import synthetic
            synthetic.scan(sim, d)  # adds made-up Reddit mentions for the day
        predict.resolve(sim)
        if d <= all_days[-6]:
            def has_history(t, d=d):
                r = sim.execute("SELECT COUNT(*), MAX(date) FROM prices WHERE ticker=?", (t,)).fetchone()
                return r[0] >= config.MIN_HISTORY_DAYS and r[1] == d
            picks = scanner.lock_picks(sim, d, _candidates(sim, d, pool), has_history)
            predict.make_predictions(sim, picks, today=date.fromisoformat(d) + timedelta(days=1))
    picked = [r[0] for r in sim.execute("SELECT DISTINCT ticker FROM selections")]
    backtest.run(sim, picked)
    db.log_run(sim, "replay", "ok", f"{days} trading days replayed")
    note = note or (f"These calls were not made live. We replayed the tracker over {days} trading "
                    f"days starting {shortdate(all_days[first_idx])}, picking each day from a pool of "
                    f"{len(pool)} small stocks by unusual trading, using only data available each morning.")
    return report.write(sim, out_dir or config.DOCS_DIR / "replay", simulation=note)


def shortdate(s):
    d = date.fromisoformat(s)
    return d.strftime("%b ") + str(d.day)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--fetch", action="store_true", help="download fresh prices for the pool")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    source = None if (config.OFFLINE or a.fetch) else str(config.DB_PATH)
    print(replay(a.days, source, Path(a.out) if a.out else None, fetch=a.fetch))
