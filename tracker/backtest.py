"""Replay the rules over past data for an early read on whether they beat simple baselines.

Limits worth knowing:
- Reddit and FINRA short-volume history are not available for free, so those rules
  never fire here; Google Trends only covers the last ~3 months.
- The watchlist was picked today, so stocks that collapsed and delisted are missing
  (survivorship bias). Treat results as a rough read, not proof.
"""
import json
import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import config, model, signals

log = logging.getLogger(__name__)
WARMUP = 60


def run(conn, tickers=None):
    tickers = tickers or config.WATCHLIST
    mkt = pd.read_sql_query("SELECT date, close FROM prices WHERE ticker=? ORDER BY date", conn,
                            params=(config.MARKET_TICKER,))
    mkt = mkt.set_index("date")["close"]
    mkt.index = pd.to_datetime(mkt.index)
    records = []
    for t in tickers:
        f = signals.build(conn, t)
        if len(f) < WARMUP + 10:
            continue
        close = f["close"].values
        for label, n in config.HORIZONS.items():
            fwd = np.full(len(f), np.nan)
            fwd[:-n] = close[n:] / close[:-n] - 1
            mprev = mkt.pct_change(n).reindex(f.index).values  # market's past move (known)
            for i in range(WARMUP, len(f) - n):
                s = f.iloc[i]
                p = model.predict(s, label)
                records.append({
                    "ticker": t, "date": f.index[i], "horizon": label,
                    "up": p["direction"] == "up", "conf": p["confidence"], "ret": fwd[i],
                    "mkt_prev": mprev[i], "pump": p["pump_flag"],
                    "rules": [r["rule"] for r in p["reasons"]],
                })
    if not records:
        return None
    df = pd.DataFrame(records)
    summary = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "tickers": tickers, "start": str(df["date"].min().date()),
               "end": str(df["date"].max().date()), "horizons": {}}
    weights = {r[0]: (r[1], r[3], r[4]) for r in model.RULES}
    for label, g in df.groupby("horizon"):
        g = g[g["ret"].notna() & (g["ret"] != 0)]
        hit = ((g["ret"] > 0) == g["up"])
        sign = np.where(g["up"], 1, -1)
        follow = g["mkt_prev"].notna()
        rules = []
        for name, (why, w1, w2) in weights.items():
            w = w1 if label == "1d" else w2
            m = g["rules"].apply(lambda rs: name in rs)
            sub = g[m]
            if len(sub) == 0:
                rules.append({"rule": name, "why": why, "fired": 0})
                continue
            pred_up = w > 0
            rules.append({
                "rule": name, "why": why, "fired": int(len(sub)),
                "says": "up" if pred_up else "down",
                "hit_rate": float(((sub["ret"] > 0) == pred_up).mean()),
                "avg_return": float(sub["ret"].mean()),
            })
        conf_buckets = []
        for lo, hi in [(0, 0.1), (0.1, 0.25), (0.25, 0.5), (0.5, 1.01)]:
            sub = g[(g["conf"] >= lo) & (g["conf"] < hi)]
            if len(sub):
                conf_buckets.append({"range": f"{lo:.2f}-{min(hi, 1):.2f}", "n": int(len(sub)),
                                     "hit_rate": float(((sub["ret"] > 0) == sub["up"]).mean())})
        pumps = g[g["pump"] == 1]
        summary["horizons"][label] = {
            "n": int(len(g)),
            "hit_rate": float(hit.mean()),
            "always_up": float((g["ret"] > 0).mean()),
            "always_down": float((g["ret"] < 0).mean()),
            "follow_market": float(((g.loc[follow, "ret"] > 0) ==
                                    (g.loc[follow, "mkt_prev"] > 0)).mean()) if follow.any() else None,
            "strategy_avg_return": float((sign * g["ret"]).mean()),
            "buy_hold_avg_return": float(g["ret"].mean()),
            "pump_flags": int(len(pumps)),
            "pump_then_down": float((pumps["ret"] < 0).mean()) if len(pumps) else None,
            "rules": rules,
            "confidence": conf_buckets,
        }
    conn.execute("INSERT INTO backtest (created_at, summary) VALUES (?,?)",
                 (summary["created_at"], json.dumps(summary)))
    conn.commit()
    return summary
