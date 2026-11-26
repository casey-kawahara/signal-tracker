"""Rule-based predictor. Each rule nudges the odds of the stock closing higher.

Kept deliberately simple and readable so it is clear why each call was made.
The backtest reports how each rule did on past data, so weak rules can be dropped.
"""
import math

import numpy as np

BASE = -0.05  # small caps drift down on average


def _ok(x):
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def _get(s, k):
    v = s.get(k) if hasattr(s, "get") else s[k]
    if v is None:
        return None
    v = float(v)
    return None if np.isnan(v) else v


def is_pump(s):
    """A buzz or volume spike with a big price jump and no filing behind it."""
    buzz = any((_get(s, k) or 0) > 2 for k in ("mentions_z", "trends_z")) or \
        (_get(s, "volume_z") or 0) > 3
    jump = (_get(s, "ret_1d") or 0) > 0.10 or (_get(s, "ret_5d") or 0) > 0.30
    no_news = (_get(s, "filings_3d") or 0) == 0
    return buzz and jump and no_news


# (name, description, test(signals) -> bool, weight_1d, weight_1w)
RULES = [
    ("pump_pattern", "Buzz or volume spike plus a price jump with no news or filing behind it",
     is_pump, -0.5, -0.8),
    ("overextended", "Up more than 30% in 5 days",
     lambda s: (_get(s, "ret_5d") or 0) > 0.30, -0.3, -0.5),
    ("capitulation", "Down more than 25% in 5 days on heavy volume",
     lambda s: (_get(s, "ret_5d") or 0) < -0.25 and (_get(s, "volume_z") or 0) > 1, 0.3, 0.3),
    ("dilution", "Offering or share-sale filing in the last 30 days",
     lambda s: (_get(s, "dilution_30d") or 0) > 0, -0.3, -0.5),
    ("reverse_split", "Reverse split in the last 60 days",
     lambda s: (_get(s, "reverse_split_60d") or 0) > 0, -0.3, -0.5),
    ("insider_buying", "Insiders bought more shares than they sold in the last 30 days",
     lambda s: (_get(s, "insider_net_30d") or 0) > 0 and (_get(s, "insider_buys_30d") or 0) > 0,
     0.2, 0.4),
    ("uptrend", "Above its 20-day average and up over 20 days",
     lambda s: (_get(s, "dist_ma20") or 0) > 0 and (_get(s, "ret_20d") or 0) > 0, 0.15, 0.2),
    ("downtrend", "Below its 20-day average and down over 20 days",
     lambda s: (_get(s, "dist_ma20") or 0) < 0 and (_get(s, "ret_20d") or 0) < 0, -0.15, -0.2),
    ("oversold", "RSI below 25",
     lambda s: _ok(_get(s, "rsi14")) and _get(s, "rsi14") < 25, 0.2, 0.15),
    ("overbought", "RSI above 80",
     lambda s: _ok(_get(s, "rsi14")) and _get(s, "rsi14") > 80, -0.2, -0.25),
    ("buzz_with_news", "Reddit buzz rising with positive tone and a fresh filing behind it",
     lambda s: (_get(s, "mentions_z") or 0) > 1.5 and (_get(s, "sentiment") or 0) > 0.2
     and (_get(s, "filings_3d") or 0) > 0, 0.2, 0.1),
    ("short_pressure", "Short-sale share of volume spiking while the price rises",
     lambda s: (_get(s, "short_ratio_z") or 0) > 2 and (_get(s, "ret_1d") or 0) > 0, 0.15, 0.1),
]

SIGNAL_KEYS = ["close", "ret_1d", "ret_5d", "ret_20d", "dist_ma20", "rsi14", "volume_z",
               "filings_3d", "dilution_30d", "insider_net_30d", "reverse_split_60d",
               "mentions", "mentions_z", "sentiment", "trends_z", "short_ratio",
               "short_ratio_z", "short_pct_float"]


def predict(s, horizon):
    col = 3 if horizon == "1d" else 4
    logit = BASE
    fired = []
    for rule in RULES:
        try:
            hit = bool(rule[2](s))
        except Exception:
            hit = False
        if hit:
            logit += rule[col]
            fired.append({"rule": rule[0], "why": rule[1], "weight": rule[col]})
    p = 1 / (1 + math.exp(-logit))
    return {
        "direction": "up" if p >= 0.5 else "down",
        "prob_up": round(p, 4),
        "confidence": round(abs(p - 0.5) * 2, 4),
        "reasons": fired,
        "pump_flag": int(is_pump(s)),
    }


def snapshot(s):
    out = {}
    for k in SIGNAL_KEYS:
        v = _get(s, k) if k in s else None
        out[k] = None if v is None else round(v, 5)
    return out
