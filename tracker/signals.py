"""Turn raw data into per-day signals. Every value for day D uses only data known by D's close,
so the same code drives live predictions and the backtest without peeking ahead."""
import numpy as np
import pandas as pd

from . import config


def _series(conn, sql, ticker, cols):
    df = pd.read_sql_query(sql, conn, params=(ticker,))
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def _zscore(s, window=20, min_periods=5):
    prior = s.shift(1)
    mean = prior.rolling(window, min_periods=min_periods).mean()
    std = prior.rolling(window, min_periods=min_periods).std()
    return (s - mean) / std.replace(0, np.nan)


def _rsi(close, n=14):
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / down.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _window_count(dates, events, days):
    """For each date, count events in the trailing `days` calendar days (inclusive)."""
    if len(events) == 0:
        return pd.Series(0.0, index=dates)
    ev = np.sort(events.values.astype("datetime64[D]"))
    d = dates.values.astype("datetime64[D]")
    hi = np.searchsorted(ev, d, side="right")
    lo = np.searchsorted(ev, d - np.timedelta64(days - 1, "D"), side="left")
    return pd.Series((hi - lo).astype(float), index=dates)


def _window_sum(dates, events, values, days):
    if len(events) == 0:
        return pd.Series(0.0, index=dates)
    order = np.argsort(events.values)
    ev = events.values[order].astype("datetime64[D]")
    cs = np.concatenate([[0.0], np.cumsum(np.asarray(values, float)[order])])
    d = dates.values.astype("datetime64[D]")
    hi = np.searchsorted(ev, d, side="right")
    lo = np.searchsorted(ev, d - np.timedelta64(days - 1, "D"), side="left")
    return pd.Series(cs[hi] - cs[lo], index=dates)


def build(conn, ticker):
    px = _series(conn, "SELECT date, close, volume FROM prices WHERE ticker=? ORDER BY date",
                 ticker, ["close", "volume"])
    if len(px) < 30:
        return pd.DataFrame()
    f = pd.DataFrame(index=px.index)
    c, v = px["close"], px["volume"]
    f["close"] = c
    f["ret_1d"] = c.pct_change()
    f["ret_5d"] = c.pct_change(5)
    f["ret_20d"] = c.pct_change(20)
    f["dist_ma20"] = c / c.rolling(20).mean() - 1
    f["rsi14"] = _rsi(c)
    f["volume_z"] = _zscore(np.log1p(v))
    f["dollar_volume"] = (c * v).rolling(20).mean()

    # SEC filings (by filing date)
    fl = pd.read_sql_query("SELECT form, filed, items, insider_buy_shares b, insider_sell_shares s "
                           "FROM filings WHERE ticker=?", conn, params=(ticker,))
    fl["filed"] = pd.to_datetime(fl["filed"])
    idx = f.index
    is8k = fl["form"] == "8-K"
    items = fl["items"].fillna("")
    f["filings_3d"] = _window_count(idx, fl.loc[is8k | fl["form"].isin(config.DILUTION_FORMS),
                                                "filed"], 3)
    dil = fl["form"].isin(config.DILUTION_FORMS) | (is8k & items.str.contains("3.02"))
    f["dilution_30d"] = _window_count(idx, fl.loc[dil, "filed"], 30)
    f4 = fl[fl["form"] == "4"]
    f["insider_net_30d"] = (_window_sum(idx, f4["filed"], f4["b"], 30)
                            - _window_sum(idx, f4["filed"], f4["s"], 30))
    f["insider_buys_30d"] = _window_count(idx, f4.loc[f4["b"] > 0, "filed"], 30)

    # reverse splits (ratio < 1 means shares were consolidated)
    sp = pd.read_sql_query("SELECT date FROM splits WHERE ticker=? AND ratio < 1", conn,
                           params=(ticker,))
    rs_events = pd.to_datetime(sp["date"])
    rs_8k = fl.loc[is8k & items.str.contains("5.03"), "filed"]  # charter amendments, often splits
    f["reverse_split_60d"] = _window_count(idx, rs_events, 60)
    f["charter_change_60d"] = _window_count(idx, rs_8k, 60)

    # Reddit buzz from ApeWisdom (only exists from the day collection started)
    bz = _series(conn, "SELECT date, mentions, mentions_prev FROM buzz WHERE ticker=?",
                 ticker, ["mentions", "mentions_prev"])
    if not bz.empty:
        bz = bz.reindex(idx)
        f["mentions"] = bz["mentions"]
        f["mentions_z"] = _zscore(bz["mentions"].astype(float), min_periods=5)
        f["mention_jump"] = (bz["mentions"] + 3) / (bz["mentions_prev"] + 3)
    else:
        f["mentions"] = f["mentions_z"] = f["mention_jump"] = np.nan
    # Tone of Reddit posts (needs the optional Reddit app keys)
    rd = _series(conn, "SELECT date, sentiment FROM reddit_daily WHERE ticker=?", ticker,
                 ["sentiment"])
    f["sentiment"] = rd["sentiment"].reindex(idx) if not rd.empty else np.nan

    # Google Trends
    tr = _series(conn, "SELECT date, interest FROM trends_daily WHERE ticker=?", ticker,
                 ["interest"])
    if not tr.empty:
        s = tr["interest"].astype(float)
        f["trends_z"] = _zscore(s, window=30, min_periods=10).reindex(idx)
    else:
        f["trends_z"] = np.nan

    # FINRA short-sale share of volume
    sv = _series(conn, "SELECT date, short_volume, total_volume FROM short_volume WHERE ticker=?",
                 ticker, ["short_volume", "total_volume"])
    if not sv.empty:
        ratio = (sv["short_volume"] / sv["total_volume"].replace(0, np.nan)).reindex(idx)
        f["short_ratio"] = ratio
        f["short_ratio_z"] = _zscore(ratio, min_periods=5)
    else:
        f["short_ratio"] = f["short_ratio_z"] = np.nan

    si = _series(conn, "SELECT date, short_pct_float FROM short_interest WHERE ticker=?",
                 ticker, ["short_pct_float"])
    f["short_pct_float"] = (si["short_pct_float"].reindex(idx, method="ffill")
                            if not si.empty else np.nan)
    return f


def latest(conn, ticker):
    f = build(conn, ticker)
    if f.empty:
        return None, None
    return f.index[-1].strftime("%Y-%m-%d"), f.iloc[-1]
