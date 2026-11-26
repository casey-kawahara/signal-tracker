"""Replay the last N trading days as if the tracker had run each morning, then score the calls.

This is a simulation for previewing the scorecard. It never touches the real prediction log:
it copies the raw data into a throwaway database and writes the page to docs/replay/.

    python scripts/replay.py --days 30            # real data from data/tracker.db
    TRACKER_OFFLINE=1 python scripts/replay.py    # fake data, no internet needed

Caveats: Reddit and SEC data only exist from the day collection started, so rules that
need them rarely fire in a replay; Google Trends and short volume cover limited history.
"""
import argparse
import os
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import backtest, config, db, predict, report  # noqa: E402

RAW = ("filings", "trends_daily", "short_volume", "splits", "reddit_daily", "short_interest")


def _copy_all(src, dst, table):
    rows = src.execute(f"SELECT * FROM {table}").fetchall()
    if rows:
        dst.executemany(f"INSERT OR REPLACE INTO {table} VALUES ({','.join('?' * len(rows[0]))})",
                        [tuple(r) for r in rows])


def replay(days=30, source=None, out_dir=None, note=None):
    tmp = Path(tempfile.mkdtemp())
    if source is None:
        from tracker.sources import finra, prices, sec, synthetic, trends
        src = db.connect(tmp / "full.db")
        prices.fetch(src, config.WATCHLIST + [config.MARKET_TICKER])
        sec.fetch(src, config.WATCHLIST)
        trends.fetch(src, config.WATCHLIST)
        finra.fetch(src, config.WATCHLIST)
        synth_reddit = True
    else:
        src = sqlite3.connect(source)
        src.row_factory = sqlite3.Row
        synth_reddit = False
    sim = db.connect(tmp / "sim.db")
    for t in RAW:
        _copy_all(src, sim, t)
    all_days = [r[0] for r in src.execute(
        "SELECT DISTINCT date FROM prices WHERE ticker=? ORDER BY date", (config.MARKET_TICKER,))]
    copied = "0000"
    for d in all_days[-(days + 5):]:  # +5 so the last week-ahead calls can be scored
        rows = src.execute("SELECT * FROM prices WHERE date > ? AND date <= ?", (copied, d)).fetchall()
        sim.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)", [tuple(r) for r in rows])
        copied = d
        if synth_reddit:
            from tracker.sources import reddit
            reddit.fetch(sim, config.WATCHLIST, d)
        predict.resolve(sim)
        if d <= all_days[-6]:
            predict.make_predictions(sim, today=date.fromisoformat(d) + timedelta(days=1))
    backtest.run(sim)
    db.log_run(sim, "replay", "ok", f"{days} trading days replayed")
    first = all_days[-(days + 5)]
    note = note or (f"These calls were not made live. We replayed the tracker over {days} trading "
                    f"days starting {first}, using only data available each morning, then scored.")
    return report.write(sim, out_dir or config.DOCS_DIR / "replay", simulation=note)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--source", default=None if config.OFFLINE else str(config.DB_PATH))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    print(replay(a.days, a.source, Path(a.out) if a.out else None))
