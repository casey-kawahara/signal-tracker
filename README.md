# Signal Tracker

An experiment: can free "alternative" signals (Reddit buzz, SEC filings, short-sale volume,
Google Trends, price and volume patterns) predict short-term moves in small-cap and penny stocks?

Every weekday before the US market opens, the tracker:

1. **Collects** the latest data for a 10-stock watchlist (all free sources, no paid APIs).
2. **Scores** earlier predictions whose horizon has passed.
3. **Locks** new predictions for each stock: up or down, next day and next week, with a
   probability, a confidence level, the rules that fired and every signal value it used.
4. **Rebuilds** a scorecard page that compares the hit rate against baselines that need no skill.

Predictions can never be edited after the fact: the database blocks updates and deletes, each
row is hash-chained to the one before it (`python -m tracker verify` checks this), and the daily
commit on GitHub timestamps everything publicly.

## Data sources

| Source | What it gives | Notes |
| --- | --- | --- |
| Yahoo Finance (`yfinance`) | Daily prices, volume, splits, short % of float | Unofficial but free |
| SEC EDGAR | 8-K events, offering filings (S-1, S-3, 424B), Form 4 insider buys and sells | Needs a contact email in `SEC_USER_AGENT` |
| Reddit | Daily mention counts and a simple bullish/bearish word score | Best with a free Reddit app (see below). No history, so it builds up from day one |
| FINRA | Daily short-sale volume | Free daily files |
| Google Trends (`pytrends`) | Search interest for "TICKER stock" | Unofficial, sometimes rate-limited |

If a source fails on a given day, the others still run and its rules simply don't fire.

## The rules

The predictor is a small set of readable rules in [`tracker/model.py`](tracker/model.py). Each one
nudges the odds of the stock closing higher. Examples: a **pump pattern** (buzz or volume spike
plus a big price jump with no filing behind it) leans down; a recent **offering filing** or
**reverse split** leans down; **insider buying** leans up. The backtest shows how each rule did
on its own, so weak ones can be dropped or reweighted.

## Scorecard

The page in `docs/index.html` shows:

- Hit rate per horizon, overall and for the last 20 trading days
- The same against **always "up"** and **follow the market** (predict each stock moves the way SPY just did)
- Size of moves: average return from following the calls vs buy-and-hold and SPY,
  and the average move when right vs when wrong
- Hit rate by confidence level, today's calls with reasons, flagged pump patterns,
  and the backtest with per-rule results

## Setup (one time)

1. **Add a secret** in *Settings → Secrets and variables → Actions*:
   - `SEC_USER_AGENT`: a name and email, e.g. `signal-tracker you@example.com` (SEC requires this)
   - Optional, for reliable Reddit data: create a "script" app at
     <https://www.reddit.com/prefs/apps> and add `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET`.
2. **Turn on the page** in *Settings → Pages*: source "Deploy from a branch", branch `main`,
   folder `/docs`. (GitHub Pages on a private repo needs a paid plan; otherwise open
   `docs/index.html` from a clone.)
3. **Run it once** from *Actions → Daily predictions → Run workflow*. The first run downloads
   three years of prices and runs the backtest. After that it runs each weekday at 12:30 UTC.

## Running locally

```bash
pip install -r requirements.txt
export SEC_USER_AGENT="signal-tracker you@example.com"
python -m tracker daily      # collect, score, predict, rebuild the page
python -m tracker backtest   # replay the rules over past data
python -m tracker verify     # check no prediction was changed
open docs/index.html
```

Try it with fake data and no internet: `python scripts/demo.py` writes `demo/index.html`.
Tests: `pip install pytest && pytest`.

To change the watchlist, edit `WATCHLIST` in [`tracker/config.py`](tracker/config.py).

## Honest caveats

- Small samples swing a lot. Expect a few weeks before the hit rate means anything.
- The backtest has no Reddit or short-volume history, and uses today's watchlist, so stocks
  that collapsed and delisted are missing (survivorship bias).
- This is a research experiment, not investment advice.
