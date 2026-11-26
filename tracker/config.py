"""Settings for the tracker. Edit WATCHLIST to change which stocks are followed."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("TRACKER_DB", ROOT / "data" / "tracker.db"))
DOCS_DIR = ROOT / "docs"

# About 10 small-cap / penny stocks that tend to trade on attention, not fundamentals.
# Tickers that stop trading are skipped with a warning; swap them out here.
WATCHLIST = [
    "SNDL", "BBAI", "PLUG", "OPEN", "TLRY",
    "CLOV", "FCEL", "MVIS", "KOSS", "ACHR",
]
MARKET_TICKER = "SPY"  # used for the "overall market" baseline

HORIZONS = {"1d": 1, "1w": 5}  # label -> trading days ahead

# SEC asks every client to identify itself with a name and contact email.
# Set SEC_USER_AGENT as a GitHub secret, e.g. "signal-tracker you@example.com".
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "signal-tracker research contact@example.com")

# Optional: a Reddit "script" app (https://www.reddit.com/prefs/apps) gives reliable access.
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
REDDIT_SUBS = ["pennystocks", "wallstreetbets", "stocks", "smallstreetbets", "RobinHoodPennyStocks"]

# Offline mode generates synthetic data so the pipeline can be tried without internet.
OFFLINE = os.environ.get("TRACKER_OFFLINE") == "1"

# Filing types that usually mean new shares are being sold (dilution).
DILUTION_FORMS = ("S-1", "S-1/A", "S-3", "S-3/A", "F-1", "F-3", "424B1", "424B2", "424B3",
                  "424B4", "424B5", "424B7")
