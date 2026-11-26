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

from . import backtest, config, db, predict, report, scanner
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
        elif out == "skipped":
            db.log_run(conn, name, "skipped", "not set up")
        elif isinstance(out, list):
            db.log_run(conn, name, "ok" if out else "error", f"{len(out)} found")
        else:
            db.log_run(conn, name, "ok", "" if out is None else out)
        return out
    except Exception as e:  # one bad source should not stop the rest
        log.exception("%s failed", name)
        db.log_run(conn, name, "error", repr(e))
        return None


def pending_tickers(conn):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT p.ticker FROM predictions p LEFT JOIN outcomes o "
        "ON o.prediction_id = p.id WHERE o.prediction_id IS NULL")]


def has_history(conn, as_of):
    def check(t):
        r = conn.execute("SELECT COUNT(*), MAX(date) FROM prices WHERE ticker=?", (t,)).fetchone()
        return r[0] >= config.MIN_HISTORY_DAYS and r[1] == as_of
    return check


def collect(conn):
    """Pull data, then pick and lock today's stocks. Returns today's picks."""
    # 1. Market + stocks still waiting to be scored
    _step(conn, "prices", prices.fetch, conn, [config.MARKET_TICKER] + pending_tickers(conn))
    as_of = conn.execute("SELECT MAX(date) FROM prices WHERE ticker=?",
                         (config.MARKET_TICKER,)).fetchone()[0]
    if not as_of:
        log.error("no market data; stopping")
        return []
    # 2. Scan for the stocks getting the most unusual attention
    ranked = _step(conn, "scan", scanner.scan, conn, as_of) or []
    shortlist = [c["ticker"] for c in ranked[:config.PICKS_PER_DAY * 2]]
    _step(conn, "prices_shortlist", prices.fetch, conn,
          [t for t in shortlist + config.WATCHLIST if t not in pending_tickers(conn)])
    # 3. Lock today's picks before predicting anything
    picks = _step(conn, "pick", scanner.lock_picks, conn, as_of, ranked,
                  has_history(conn, as_of)) or []
    # 4. Extra signals for today's picks
    _step(conn, "sec", sec.fetch, conn, picks)
    _step(conn, "reddit_tone", reddit.fetch, conn, picks, as_of)
    _step(conn, "finra_short_volume", finra.fetch, conn, picks)
    _step(conn, "short_interest", prices.short_interest, conn, picks, as_of)
    _step(conn, "google_trends", trends.fetch, conn, picks)
    return picks


def todays_picks(conn):
    as_of = conn.execute("SELECT MAX(as_of) FROM selections").fetchone()[0]
    return [r[0] for r in conn.execute("SELECT ticker FROM selections WHERE as_of=? ORDER BY rank",
                                       (as_of,))]


def _recent_picks(conn, days=60):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM selections WHERE as_of >= date('now', ?)", (f"-{days} days",))]


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
        _step(conn, "predict", predict.make_predictions, conn, todays_picks(conn) or None)
    if cmd in ("backtest", "daily"):  # takes seconds, so refresh it every run
        s = backtest.run(conn, sorted(set(todays_picks(conn)) | set(_recent_picks(conn))) or None)
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
