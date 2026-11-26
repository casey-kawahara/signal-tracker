"""SEC EDGAR (free, no key): 8-K events, offering filings and insider Form 4 trades."""
import logging
import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta

import requests

from .. import config

log = logging.getLogger(__name__)
_session = None
_cik_map = None


def _get(url):
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = config.SEC_USER_AGENT
    time.sleep(0.15)  # SEC allows 10 requests/second; stay well under
    r = _session.get(url, timeout=30)
    r.raise_for_status()
    return r


def cik_for(ticker):
    global _cik_map
    if _cik_map is None:
        data = _get("https://www.sec.gov/files/company_tickers.json").json()
        _cik_map = {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}
    return _cik_map.get(ticker.upper())


def _form4_trades(cik, accession, primary_doc):
    """Sum open-market buys (code P) and sells (code S) in a Form 4."""
    doc = primary_doc.split("/")[-1]  # drop the xsl rendering prefix to get raw XML
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{doc}"
    buy = sell = 0.0
    try:
        root = ET.fromstring(_get(url).content)
    except Exception as e:
        log.debug("form4 parse failed %s: %s", url, e)
        return buy, sell
    for tx in root.iter("nonDerivativeTransaction"):
        code = tx.findtext("transactionCoding/transactionCode")
        shares = tx.findtext("transactionAmounts/transactionShares/value")
        try:
            shares = float(shares)
        except (TypeError, ValueError):
            continue
        if code == "P":
            buy += shares
        elif code == "S":
            sell += shares
    return buy, sell


def fetch(conn, tickers, lookback_days=1100):
    if config.OFFLINE:
        from . import synthetic
        return synthetic.filings(conn, tickers)
    since = (date.today() - timedelta(days=lookback_days)).isoformat()
    form4_since = (date.today() - timedelta(days=120)).isoformat()
    ok = failed = 0
    for t in tickers:
        try:
            cik = cik_for(t)
            if not cik:
                log.warning("no SEC CIK for %s", t)
                failed += 1
                continue
            sub = _get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").json()
        except Exception as e:
            log.warning("SEC %s failed: %s", t, e)
            failed += 1
            continue
        recent = sub.get("filings", {}).get("recent", {})
        n = len(recent.get("accessionNumber", []))
        known = {r[0] for r in conn.execute("SELECT accession FROM filings WHERE ticker=?", (t,))}
        added = 0
        for i in range(n):
            acc = recent["accessionNumber"][i]
            form = recent["form"][i]
            filed = recent["filingDate"][i]
            if acc in known or filed < since:
                continue
            if not (form in ("8-K", "4") or form in config.DILUTION_FORMS):
                continue
            buy = sell = 0.0
            if form == "4" and filed >= form4_since:
                buy, sell = _form4_trades(cik, acc, recent["primaryDocument"][i])
            conn.execute("INSERT OR IGNORE INTO filings VALUES (?,?,?,?,?,?,?)",
                         (acc, t, form, filed, recent.get("items", [""] * n)[i], buy, sell))
            added += 1
        conn.commit()
        log.info("SEC %s: %d new filings", t, added)
        ok += 1
    return ok, failed
