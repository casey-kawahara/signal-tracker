import os
import sqlite3
from datetime import date, timedelta

os.environ["TRACKER_OFFLINE"] = "1"

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from tracker import config, db, model, predict, signals  # noqa: E402
from tracker.sources import reddit, synthetic  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    synthetic.prices(c, ["AAA", config.MARKET_TICKER], n=200)
    synthetic.filings(c, ["AAA"])
    return c


def _lock_one(conn):
    last = conn.execute("SELECT MAX(date) FROM prices").fetchone()[0]
    return predict.make_predictions(conn, ["AAA"], today=date.fromisoformat(last) + timedelta(1))


def test_predictions_cannot_be_edited_or_deleted(conn):
    assert _lock_one(conn) == 2
    with pytest.raises(sqlite3.DatabaseError, match="locked"):
        conn.execute("UPDATE predictions SET direction='up'")
    with pytest.raises(sqlite3.DatabaseError, match="locked"):
        conn.execute("DELETE FROM predictions")


def test_same_day_is_not_predicted_twice(conn):
    assert _lock_one(conn) == 2
    assert _lock_one(conn) == 0


def test_hash_chain_detects_tampering(conn):
    _lock_one(conn)
    assert db.verify_chain(conn) == (True, 2)
    conn.execute("DROP TRIGGER predictions_no_update")
    conn.execute("UPDATE predictions SET direction = CASE direction WHEN 'up' THEN 'down' "
                 "ELSE 'up' END WHERE id = 1")
    assert db.verify_chain(conn)[0] is False


def test_resolve_scores_against_later_close(conn):
    _lock_one(conn)
    p = conn.execute("SELECT * FROM predictions WHERE horizon='1d'").fetchone()
    assert predict.resolve(conn) == 0  # no future bar yet
    nxt = (date.fromisoformat(p["as_of"]) + timedelta(days=3)).isoformat()
    for t in ("AAA", config.MARKET_TICKER):
        conn.execute("INSERT INTO prices VALUES (?,?,?,?,?,?,?)", (t, nxt, 1, 1, 1,
                     p["base_close"] * 1.1 if t == "AAA" else 500, 1e6))
    assert predict.resolve(conn) == 1
    o = conn.execute("SELECT * FROM outcomes").fetchone()
    assert o["actual_return"] == pytest.approx(0.1)
    assert o["correct"] == (1 if p["direction"] == "up" else 0)


def test_signals_do_not_peek_ahead(conn):
    full = signals.build(conn, "AAA")
    cut = full.index[150].strftime("%Y-%m-%d")
    conn.execute("DELETE FROM prices WHERE ticker='AAA' AND date > ?", (cut,))
    part = signals.build(conn, "AAA")
    cols = ["ret_1d", "ret_5d", "dist_ma20", "rsi14", "volume_z", "dilution_30d"]
    pd.testing.assert_series_equal(full.loc[cut, cols], part.loc[cut, cols], check_names=False)


def test_pump_rule():
    s = {"mentions_z": 3.0, "ret_1d": 0.25, "filings_3d": 0, "volume_z": 1}
    assert model.is_pump(s)
    assert not model.is_pump({**s, "filings_3d": 1})  # news explains it
    p = model.predict(s, "1w")
    assert p["direction"] == "down" and p["pump_flag"] == 1


def test_missing_signals_are_neutral():
    p = model.predict({"close": 1.0}, "1d")
    assert p["reasons"] == [] and 0.45 < p["prob_up"] < 0.5


def test_sentiment():
    assert reddit.sentiment("to the moon 🚀 buying calls") > 0
    assert reddit.sentiment("another dilution offering, total scam") < 0


def test_demo_end_to_end(tmp_path):
    import scripts.demo as demo
    out = demo.simulate(days=12, out_dir=tmp_path)
    html = out.read_text()
    assert "Signal Tracker" in html and "/*__DATA__*/null" not in html
