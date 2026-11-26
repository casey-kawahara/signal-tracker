"""Settings for the tracker. Edit WATCHLIST to change which stocks are followed."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("TRACKER_DB", ROOT / "data" / "tracker.db"))
DOCS_DIR = ROOT / "docs"

# Stocks are picked automatically each morning (see tracker/scanner.py). This list is only a
# backup, used when the scan finds fewer than PICKS_PER_DAY stocks.
WATCHLIST = [
    "SNDL", "BBAI", "PLUG", "OPEN", "TLRY",
    "CLOV", "FCEL", "MVIS", "KOSS", "ACHR",
]
PICKS_PER_DAY = 10
MIN_PRICE = 0.20             # dollars; below this, quotes get unreliable
MIN_MARKET_CAP = 20e6        # $20M
MAX_MARKET_CAP = 2e9         # $2B: the usual cutoff for "small cap"
MIN_VOLUME = 300_000         # shares a day, so the stock is actually tradable
MIN_HISTORY_DAYS = 30        # trading days of prices needed to make a call

# Stocks used by the practice-run replay, which can't scan the past. A mix of well-known
# small and penny stocks; ones that stopped trading are skipped.
REPLAY_POOL = WATCHLIST + [
    "AMC", "BB", "SENS", "OCGN", "WKHS", "SPCE", "BYND", "QS", "CHPT", "BLNK",
    "TELL", "JOBY", "LUNR", "DNA", "RIG", "CIFR", "WULF", "HIVE", "BITF", "GPRO",
    "FUBO", "NKTR", "AEHR", "SOUN", "QUBT", "RGTI", "LCID", "RUN", "SIRI", "NIO",
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
