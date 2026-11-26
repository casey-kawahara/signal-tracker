"""Build a demo scorecard from synthetic data, replaying 40 trading days of live predictions.

    TRACKER_OFFLINE=1 python scripts/demo.py   -> writes demo/index.html
"""
import os
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

os.environ["TRACKER_OFFLINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import backtest, config, db, predict, report  # noqa: E402
from tracker.sources import finra, prices, reddit, sec, synthetic, trends  # noqa: E402


def simulate(days=40, out_dir=None):
    tmp = Path(tempfile.mkdtemp())
    full = db.connect(tmp / "full.db")
    tickers = config.WATCHLIST
    prices.fetch(full, tickers + [config.MARKET_TICKER])
    sec.fetch(full, tickers)
    trends.fetch(full, tickers)
    finra.fetch(full, tickers)
    all_days = [r[0] for r in full.execute(
        "SELECT DISTINCT date FROM prices WHERE ticker=? ORDER BY date", (config.MARKET_TICKER,))]
    live = db.connect(tmp / "live.db")
    for tbl in ("filings", "trends_daily", "short_volume", "splits"):
        rows = full.execute(f"SELECT * FROM {tbl}").fetchall()
        if rows:
            live.executemany(f"INSERT OR REPLACE INTO {tbl} VALUES ({','.join('?' * len(rows[0]))})",
                             [tuple(r) for r in rows])
    start = len(all_days) - days
    copied = None
    for i in range(start, len(all_days)):
        d = all_days[i]
        rows = full.execute("SELECT * FROM prices WHERE date > ? AND date <= ?",
                            (copied or "0000", d)).fetchall()
        live.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)", [tuple(r) for r in rows])
        copied = d
        reddit.fetch(live, tickers, d)
        predict.resolve(live)
        predict.make_predictions(live, today=date.fromisoformat(d) + timedelta(days=1))
    backtest.run(live)
    db.log_run(live, "demo", "ok", "synthetic data")
    return report.write(live, out_dir or config.ROOT / "demo")


if __name__ == "__main__":
    print(simulate())
