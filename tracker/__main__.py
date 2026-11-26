"""Command line: python -m tracker <command>

  daily     collect data, score old predictions, lock new ones, rebuild the page
  collect   only pull data
  predict   only lock new predictions
  resolve   only score predictions whose horizon has passed
  backtest  replay the rules over past data
  report    rebuild docs/index.html
  verify    check that no locked prediction was changed
"""
import argparse
import logging
import sys
from datetime import date

from . import backtest, config, db, predict, report
from .sources import finra, prices, reddit, sec, trends

log = logging.getLogger("tracker")


def _step(conn, name, fn, *a):
    try:
        out = fn(*a)
        if isinstance(out, tuple) and len(out) == 2:
            good, bad = out
            status = "ok" if bad == 0 else ("partial" if good else "error")
            db.log_run(conn, name, status, f"{good} ok, {bad} failed (see Actions log)" if bad
                       else f"{good} ok")
        else:
            db.log_run(conn, name, "ok", "" if out is None else out)
        return out
    except Exception as e:  # one bad source should not stop the rest
        log.exception("%s failed", name)
        db.log_run(conn, name, "error", repr(e))
        return None


def collect(conn):
    tickers = config.WATCHLIST
    _step(conn, "prices", prices.fetch, conn, tickers + [config.MARKET_TICKER])
    last = conn.execute("SELECT MAX(date) FROM prices WHERE ticker=?",
                        (config.MARKET_TICKER,)).fetchone()[0] or date.today().isoformat()
    _step(conn, "sec", sec.fetch, conn, tickers)
    _step(conn, "reddit", reddit.fetch, conn, tickers, last)
    _step(conn, "finra_short_volume", finra.fetch, conn, tickers)
    _step(conn, "short_interest", prices.short_interest, conn, tickers, last)
    _step(conn, "google_trends", trends.fetch, conn, tickers)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tracker", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["daily", "collect", "predict", "resolve", "backtest",
                                        "report", "verify"])
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    conn = db.connect()
    cmd = args.command
    if cmd in ("daily", "collect"):
        collect(conn)
    if cmd in ("daily", "resolve"):
        _step(conn, "resolve", predict.resolve, conn)
    if cmd in ("daily", "predict"):
        _step(conn, "predict", predict.make_predictions, conn)
    if cmd in ("backtest", "daily"):  # takes seconds, so refresh it every run
        s = backtest.run(conn)
        if s:
            for h, v in s["horizons"].items():
                log.info("backtest %s: hit %.1f%% vs always-up %.1f%% (n=%d)", h,
                         v["hit_rate"] * 100, v["always_up"] * 100, v["n"])
    if cmd == "verify":
        ok, n = db.verify_chain(conn)
        print(f"{'OK' if ok else 'BROKEN'}: {n} predictions verified")
        return 0 if ok else 1
    if cmd in ("daily", "report", "backtest", "predict", "resolve"):
        path = report.write(conn)
        log.info("wrote %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
