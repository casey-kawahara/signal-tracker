"""Lock today's predictions and score old ones once their horizon has passed."""
import json
import logging
from datetime import date, datetime, timezone

import pandas as pd

from . import config, db, model, signals

log = logging.getLogger(__name__)


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_predictions(conn, tickers=None, today=None, max_stale_days=5):
    tickers = tickers or config.WATCHLIST
    today = today or date.today()
    made = 0
    for t in tickers:
        as_of, s = signals.latest(conn, t)
        if s is None:
            log.warning("not enough data to predict %s", t)
            continue
        if (today - date.fromisoformat(as_of)).days > max_stale_days:
            log.warning("%s data is stale (last %s); skipping", t, as_of)
            continue
        snap = json.dumps(model.snapshot(s), sort_keys=True)
        for h in config.HORIZONS:
            if conn.execute("SELECT 1 FROM predictions WHERE as_of=? AND ticker=? AND horizon=?",
                            (as_of, t, h)).fetchone():
                continue  # already locked for this trading day
            p = model.predict(s, h)
            row = {
                "created_at": now_utc(), "as_of": as_of, "ticker": t, "horizon": h,
                "direction": p["direction"], "prob_up": p["prob_up"],
                "confidence": p["confidence"], "base_close": round(float(s["close"]), 6),
                "signals": snap, "reasons": json.dumps(p["reasons"]),
                "pump_flag": p["pump_flag"],
            }
            prev = db.last_hash(conn)
            row["prev_hash"] = prev
            row["hash"] = db.row_hash(prev, row)
            cols = ",".join(row)
            conn.execute(f"INSERT INTO predictions ({cols}) VALUES ({','.join('?' * len(row))})",
                         list(row.values()))
            conn.commit()
            made += 1
    log.info("locked %d new predictions", made)
    return made


def _closes(conn, ticker):
    df = pd.read_sql_query("SELECT date, close FROM prices WHERE ticker=? ORDER BY date", conn,
                           params=(ticker,))
    return df.set_index("date")["close"]


def resolve(conn):
    pending = conn.execute(
        "SELECT p.* FROM predictions p LEFT JOIN outcomes o ON o.prediction_id = p.id "
        "WHERE o.prediction_id IS NULL").fetchall()
    cache = {}
    done = 0
    for p in pending:
        n = config.HORIZONS[p["horizon"]]
        for t in (p["ticker"], config.MARKET_TICKER):
            if t not in cache:
                cache[t] = _closes(conn, t)
        c = cache[p["ticker"]]
        after = c[c.index > p["as_of"]]
        if len(after) < n:
            continue
        target_date, target = after.index[n - 1], float(after.iloc[n - 1])
        ret = target / p["base_close"] - 1
        m = cache[config.MARKET_TICKER]
        mret = None
        if p["as_of"] in m.index and target_date in m.index:
            mret = float(m[target_date] / m[p["as_of"]] - 1)
        correct = int((ret > 0 and p["direction"] == "up") or (ret < 0 and p["direction"] == "down"))
        conn.execute("INSERT INTO outcomes VALUES (?,?,?,?,?,?,?)",
                     (p["id"], now_utc(), target_date, target, ret, mret, correct))
        done += 1
    conn.commit()
    log.info("resolved %d predictions", done)
    return done
