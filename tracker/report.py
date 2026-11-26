"""Build the scorecard as one static HTML page (docs/index.html), served free by GitHub Pages."""
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import config, db

TEMPLATE = (config.ROOT / "tracker" / "template.html")


def _f(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    return float(x)


def _market_prev(conn):
    m = pd.read_sql_query("SELECT date, close FROM prices WHERE ticker=? ORDER BY date", conn,
                          params=(config.MARKET_TICKER,)).set_index("date")["close"]
    return {label: m.pct_change(n).to_dict() for label, n in config.HORIZONS.items()}


def live_stats(conn):
    df = pd.read_sql_query(
        "SELECT p.id, p.as_of, p.ticker, p.horizon, p.direction, p.confidence, p.pump_flag, "
        "o.actual_return, o.market_return, o.correct, o.target_date "
        "FROM predictions p JOIN outcomes o ON o.prediction_id = p.id ORDER BY p.as_of", conn)
    out = {}
    mprev = _market_prev(conn)
    for label in config.HORIZONS:
        g = df[df["horizon"] == label].copy()
        if g.empty:
            out[label] = {"n": 0}
            continue
        g["up"] = g["direction"] == "up"
        g["mkt_prev"] = g["as_of"].map(mprev[label])
        sign = np.where(g["up"], 1, -1)
        follow = g["mkt_prev"].notna()
        by_day = g.groupby("as_of").agg(right=("correct", "sum"), n=("correct", "size"),
                                        ups=("actual_return", lambda r: (r > 0).sum()))
        cum = by_day.cumsum()
        series = [{"date": d, "model": r.right / r.n, "always_up": r.ups / r.n}
                  for d, r in cum.iterrows()]
        conf = []
        for lo, hi, name in [(0, 0.1, "Low"), (0.1, 0.3, "Medium"), (0.3, 1.01, "High")]:
            sub = g[(g["confidence"] >= lo) & (g["confidence"] < hi)]
            if len(sub):
                conf.append({"bucket": name, "n": int(len(sub)), "hit_rate": float(sub["correct"].mean())})
        last20 = g[g["as_of"].isin(sorted(g["as_of"].unique())[-20:])]
        out[label] = {
            "n": int(len(g)),
            "hit_rate": float(g["correct"].mean()),
            "hit_rate_recent": float(last20["correct"].mean()),
            "always_up": float((g["actual_return"] > 0).mean()),
            "always_down": float((g["actual_return"] < 0).mean()),
            "follow_market": _f(((g.loc[follow, "actual_return"] > 0) ==
                                 (g.loc[follow, "mkt_prev"] > 0)).mean()) if follow.any() else None,
            "strategy_avg_return": float((sign * g["actual_return"]).mean()),
            "buy_hold_avg_return": float(g["actual_return"].mean()),
            "market_avg_return": _f(g["market_return"].mean()),
            "avg_move_right": _f(g.loc[g["correct"] == 1, "actual_return"].abs().mean()),
            "avg_move_wrong": _f(g.loc[g["correct"] == 0, "actual_return"].abs().mean()),
            "pump_flags": int(g["pump_flag"].sum()),
            "pump_then_down": _f((g.loc[g["pump_flag"] == 1, "actual_return"] < 0).mean())
            if g["pump_flag"].any() else None,
            "series": series,
            "confidence": conf,
            "right": int(g["correct"].sum()),
            "per_ticker": {t: {"n": int(len(x)), "right": int(x["correct"].sum())}
                           for t, x in g.groupby("ticker")},
        }
    return out


def _rows(conn, sql, params=()):
    rows = []
    for r in conn.execute(sql, params):
        d = dict(r)
        for k in ("signals", "reasons"):
            if k in d and d[k]:
                d[k] = json.loads(d[k])
        rows.append(d)
    return rows


def portfolio(conn, start=1000.0):
    """What $1,000 would be worth following the next-day calls, vs the S&P 500 (SPY).

    Each trading day the money is split equally across that day's "likely up" picks, held to
    the next close, then moved into the next day's picks. A day with no "up" picks sits in cash.
    A second version also bets against the "likely down" picks. Ignores trading costs and taxes.
    """
    df = pd.read_sql_query(
        "SELECT p.as_of, p.ticker, p.direction, o.target_date, o.actual_return, o.market_return, "
        "s.ticker IS NOT NULL AS picked FROM predictions p JOIN outcomes o ON o.prediction_id=p.id "
        "LEFT JOIN selections s ON s.as_of=p.as_of AND s.ticker=p.ticker WHERE p.horizon='1d' "
        "ORDER BY p.as_of", conn)
    if df.empty:
        return None
    has_picks = df.groupby("as_of")["picked"].transform("max") == 1
    df = df[~has_picks | (df["picked"] == 1)]  # on days with picks, count only the picks
    rows = []
    up_v = both_v = mkt_v = start
    for d, g in df.groupby("as_of"):
        ups = g[g["direction"] == "up"]["actual_return"]
        sign = np.where(g["direction"] == "up", 1, -1)
        r_up = float(ups.mean()) if len(ups) else 0.0
        r_both = float((sign * g["actual_return"]).mean())
        m = g["market_return"].dropna()
        r_mkt = float(m.iloc[0]) if len(m) else 0.0
        up_v *= 1 + r_up
        both_v *= 1 + r_both
        mkt_v *= 1 + r_mkt
        rows.append({"date": g["target_date"].iloc[0], "made": d, "ups": int(len(ups)),
                     "picks": int(len(g)), "r_up": r_up, "r_both": r_both, "r_mkt": r_mkt,
                     "up": round(up_v, 2), "both": round(both_v, 2), "market": round(mkt_v, 2)})
    return {"start_value": start, "start_date": rows[0]["made"], "days": len(rows),
            "series": rows, "up": round(up_v, 2), "both": round(both_v, 2),
            "market": round(mkt_v, 2),
            "beat_days": int(sum(r["r_up"] > r["r_mkt"] for r in rows))}


def build(conn):
    latest = conn.execute("SELECT MAX(as_of) FROM predictions").fetchone()[0]
    today = _rows(conn, "SELECT p.*, o.actual_return, o.correct, s.reasons AS picked_why, "
                        "s.detail AS picked_detail, s.rank AS picked_rank FROM predictions p "
                        "LEFT JOIN outcomes o ON o.prediction_id=p.id "
                        "LEFT JOIN selections s ON s.as_of=p.as_of AND s.ticker=p.ticker "
                        "WHERE p.as_of=? ORDER BY p.ticker, p.horizon", (latest,)) if latest else []
    if any(r.get("picked_why") for r in today):  # show only the day's picks as cards
        today = [r for r in today if r.get("picked_why")]
    for r in today:
        for k in ("picked_why", "picked_detail"):
            r[k] = json.loads(r[k]) if r.get(k) else None
    scan_run = conn.execute("SELECT detail FROM runs WHERE step='scan' AND status='ok' "
                            "ORDER BY id DESC LIMIT 1").fetchone()
    recent = _rows(conn, "SELECT p.id, p.created_at, p.as_of, p.ticker, p.horizon, p.direction, "
                         "p.confidence, p.pump_flag, o.target_date, o.actual_return, o.correct "
                         "FROM predictions p JOIN outcomes o ON o.prediction_id=p.id "
                         "ORDER BY o.target_date DESC, p.ticker LIMIT 300")
    pumps = _rows(conn, "SELECT p.as_of, p.ticker, p.signals, o.actual_return FROM predictions p "
                        "LEFT JOIN outcomes o ON o.prediction_id=p.id "
                        "WHERE p.pump_flag=1 AND p.horizon='1w' ORDER BY p.as_of DESC LIMIT 30")
    bt = conn.execute("SELECT summary FROM backtest ORDER BY id DESC LIMIT 1").fetchone()
    ok, count = db.verify_chain(conn)
    pending = conn.execute("SELECT COUNT(*) FROM predictions p LEFT JOIN outcomes o "
                           "ON o.prediction_id=p.id WHERE o.prediction_id IS NULL").fetchone()[0]
    runs = _rows(conn, "SELECT started_at, step, status, detail FROM runs ORDER BY id DESC LIMIT 12")
    sources = _rows(conn, "SELECT step, status, detail, started_at FROM runs r WHERE id = "
                          "(SELECT MAX(id) FROM runs WHERE step = r.step) ORDER BY id")
    data = {
        "sources": sources,
        "scanned": int(scan_run[0].split()[0]) if scan_run and scan_run[0].split()[0].isdigit() else None,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "watchlist": config.WATCHLIST,
        "latest_as_of": latest,
        "today": today,
        "live": live_stats(conn),
        "portfolio": portfolio(conn),
        "recent": recent,
        "pumps": pumps,
        "backtest": json.loads(bt["summary"]) if bt else None,
        "chain": {"ok": ok, "count": count, "pending": pending},
        "runs": runs,
    }
    return data


def write(conn, out_dir=None, simulation=None):
    out_dir = out_dir or config.DOCS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    data = build(conn)
    data["simulation"] = simulation
    blob = json.dumps(data, default=str).replace("</", "<\\/")
    html = TEMPLATE.read_text().replace("/*__DATA__*/null", blob)
    (out_dir / "index.html").write_text(html)
    (out_dir / "data.json").write_text(json.dumps(data, default=str, indent=1))
    return out_dir / "index.html"
