"""SQLite storage. Predictions and outcomes are append-only: triggers block edits and deletes."""
import hashlib
import json
import sqlite3

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS splits (
    ticker TEXT, date TEXT, ratio REAL, PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS filings (
    accession TEXT PRIMARY KEY, ticker TEXT, form TEXT, filed TEXT, items TEXT,
    insider_buy_shares REAL DEFAULT 0, insider_sell_shares REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS filings_ticker ON filings (ticker, filed);
CREATE TABLE IF NOT EXISTS reddit_daily (
    ticker TEXT, date TEXT, mentions INTEGER, score INTEGER, sentiment REAL,
    PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS trends_daily (
    ticker TEXT, date TEXT, interest REAL, PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS short_volume (
    ticker TEXT, date TEXT, short_volume REAL, total_volume REAL, PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS short_interest (
    ticker TEXT, date TEXT, short_pct_float REAL, days_to_cover REAL, PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,          -- UTC time the prediction was locked
    as_of TEXT NOT NULL,               -- last trading day whose data was used
    ticker TEXT NOT NULL,
    horizon TEXT NOT NULL,             -- '1d' or '1w'
    direction TEXT NOT NULL,           -- 'up' or 'down'
    prob_up REAL NOT NULL,
    confidence REAL NOT NULL,          -- 0..1
    base_close REAL NOT NULL,
    signals TEXT NOT NULL,             -- JSON: the signal values used
    reasons TEXT NOT NULL,             -- JSON: rules that fired and their weight
    pump_flag INTEGER NOT NULL DEFAULT 0,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL,
    UNIQUE (as_of, ticker, horizon)
);
CREATE TABLE IF NOT EXISTS outcomes (
    prediction_id INTEGER PRIMARY KEY REFERENCES predictions(id),
    resolved_at TEXT NOT NULL,
    target_date TEXT NOT NULL,
    target_close REAL NOT NULL,
    actual_return REAL NOT NULL,
    market_return REAL,
    correct INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, step TEXT, status TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS backtest (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, summary TEXT
);

CREATE TRIGGER IF NOT EXISTS predictions_no_update BEFORE UPDATE ON predictions
BEGIN SELECT RAISE(ABORT, 'predictions are locked and cannot be edited'); END;
CREATE TRIGGER IF NOT EXISTS predictions_no_delete BEFORE DELETE ON predictions
BEGIN SELECT RAISE(ABORT, 'predictions are locked and cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS outcomes_no_update BEFORE UPDATE ON outcomes
BEGIN SELECT RAISE(ABORT, 'outcomes are final'); END;
CREATE TRIGGER IF NOT EXISTS outcomes_no_delete BEFORE DELETE ON outcomes
BEGIN SELECT RAISE(ABORT, 'outcomes are final'); END;
"""

HASH_FIELDS = ("created_at", "as_of", "ticker", "horizon", "direction", "prob_up",
               "confidence", "base_close", "signals", "reasons", "pump_flag")


def connect(path=None):
    path = path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def row_hash(prev_hash, row):
    payload = json.dumps({k: row[k] for k in HASH_FIELDS}, sort_keys=True)
    return hashlib.sha256((prev_hash + payload).encode()).hexdigest()


def last_hash(conn):
    r = conn.execute("SELECT hash FROM predictions ORDER BY id DESC LIMIT 1").fetchone()
    return r["hash"] if r else "genesis"


def verify_chain(conn):
    """Return (ok, count). Any edit to a stored prediction breaks the chain."""
    prev = "genesis"
    n = 0
    for r in conn.execute("SELECT * FROM predictions ORDER BY id"):
        if r["prev_hash"] != prev or row_hash(prev, r) != r["hash"]:
            return False, n
        prev = r["hash"]
        n += 1
    return True, n


def log_run(conn, step, status, detail=""):
    from datetime import datetime, timezone
    conn.execute("INSERT INTO runs (started_at, step, status, detail) VALUES (?,?,?,?)",
                 (datetime.now(timezone.utc).isoformat(timespec="seconds"), step, status,
                  str(detail)[:500]))
    conn.commit()
