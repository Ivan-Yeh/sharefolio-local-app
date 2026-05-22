"""Disk-based price cache for historical prices and FX rates.

Fetches data from yfinance and stores it in a SQLite database.
This module never touches any in-memory Portfolio or Asset objects — callers
are responsible for rebuilding those from the loaded data.
"""

import logging
import sqlite3
import threading
import time

import pandas as pd
import yfinance as yf

from configs import SUPPORTED_FX_PAIRS
from paths import APP_DATA_DIR

logger = logging.getLogger(__name__)

CACHE_DIR = APP_DATA_DIR / "price_cache"
CACHE_DB = CACHE_DIR / "cache.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    date   TEXT NOT NULL,
    symbol TEXT NOT NULL,
    close  REAL,
    PRIMARY KEY (date, symbol)
);
CREATE TABLE IF NOT EXISTS fx (
    date TEXT NOT NULL,
    pair TEXT NOT NULL,
    close REAL,
    PRIMARY KEY (date, pair)
);
CREATE TABLE IF NOT EXISTS cache_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_fetching = False
_fetching_lock = threading.Lock()

try:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _con = sqlite3.connect(CACHE_DB)
    _con.executescript(_SCHEMA)
    _con.close()
except Exception:
    pass


def _open() -> sqlite3.Connection:
    con = sqlite3.connect(CACHE_DB)
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _ensure_schema() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    con = _open()
    con.executescript(_SCHEMA)
    con.close()


def is_ready() -> bool:
    if not CACHE_DB.exists():
        return False
    try:
        con = _open()
        count = con.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        con.close()
        return count > 0
    except Exception:
        return False


def is_fetching() -> bool:
    with _fetching_lock:
        return _fetching


def last_updated() -> float:
    if not CACHE_DB.exists():
        return 0.0
    try:
        con = _open()
        row = con.execute("SELECT value FROM cache_meta WHERE key='last_updated'").fetchone()
        con.close()
        return float(row[0]) if row else 0.0
    except Exception:
        return 0.0


def fetch_and_save(all_symbols: list[str], inception_date) -> bool:
    """Fetch prices + FX from yfinance and write to the SQLite cache.

    all_symbols: list of 'TICKER.EXCHANGE' strings (US symbols have .US suffix).
    inception_date: earliest date needed.
    Returns True on success, False on failure.
    Never modifies any in-memory object.
    """
    global _fetching
    with _fetching_lock:
        if _fetching:
            return False
        _fetching = True
    try:
        yf_symbols = [s.replace(".US", "") for s in all_symbols]
        inception_ts = pd.Timestamp(inception_date)

        prices = yf.download(
            yf_symbols,
            start=inception_ts,
            multi_level_index=False,
            auto_adjust=False,
            rounding=True,
        )["Close"]

        if len(yf_symbols) == 1 and isinstance(prices, pd.Series):
            prices = prices.to_frame(name=yf_symbols[0])

        if not prices.empty and prices.index.min() > inception_ts:
            prices = prices.reindex(
                pd.date_range(start=inception_ts, end=prices.index.max(), freq="D")
            )
        prices = prices.reindex(
            pd.date_range(start=inception_ts, end=pd.Timestamp.today(), freq="D"),
            method="ffill",
        )
        prices.ffill(inplace=True)

        fx = yf.download(
            SUPPORTED_FX_PAIRS,
            start=inception_ts,
            multi_level_index=False,
            auto_adjust=False,
            rounding=True,
        )["Close"]

        if isinstance(fx, pd.Series):
            fx = fx.to_frame(name=SUPPORTED_FX_PAIRS[0])

        fx = fx.reindex(
            pd.date_range(start=inception_ts, end=pd.Timestamp.today(), freq="D"),
            method="ffill",
        )
        fx.ffill(inplace=True)

        # Melt wide DataFrames to long format for storage
        prices_long = (
            prices.reset_index()
            .rename(columns={"index": "date"})
            .melt(id_vars="date", var_name="symbol", value_name="close")
        )
        prices_long["date"] = prices_long["date"].astype(str)

        fx_long = (
            fx.reset_index()
            .rename(columns={"index": "date"})
            .melt(id_vars="date", var_name="pair", value_name="close")
        )
        fx_long["date"] = fx_long["date"].astype(str)

        _ensure_schema()
        con = _open()
        try:
            con.execute("BEGIN EXCLUSIVE")
            con.execute("DELETE FROM prices")
            con.execute("DELETE FROM fx")
            con.executemany(
                "INSERT INTO prices (date, symbol, close) VALUES (?, ?, ?)",
                prices_long[["date", "symbol", "close"]].itertuples(index=False, name=None),
            )
            con.executemany(
                "INSERT INTO fx (date, pair, close) VALUES (?, ?, ?)",
                fx_long[["date", "pair", "close"]].itertuples(index=False, name=None),
            )
            con.execute(
                "INSERT OR REPLACE INTO cache_meta (key, value) VALUES (?, ?)",
                ("last_updated", str(time.time())),
            )
            con.execute(
                "INSERT OR REPLACE INTO cache_meta (key, value) VALUES (?, ?)",
                ("inception_date", str(inception_ts.date())),
            )
            con.execute(
                "INSERT OR REPLACE INTO cache_meta (key, value) VALUES (?, ?)",
                ("symbols", ",".join(all_symbols)),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

        return True
    except Exception:
        logger.exception("price_cache.fetch_and_save failed")
        return False
    finally:
        with _fetching_lock:
            _fetching = False


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (prices_df, fx_df) loaded from the SQLite cache.

    Both DataFrames are in wide format with a DatetimeIndex.
    Caller must check is_ready() first.
    """
    con = _open()
    try:
        prices_long = pd.read_sql("SELECT date, symbol, close FROM prices", con)
        fx_long = pd.read_sql("SELECT date, pair, close FROM fx", con)
    finally:
        con.close()

    prices = prices_long.pivot(index="date", columns="symbol", values="close")
    prices.columns.name = None
    prices.index = pd.DatetimeIndex(prices.index)

    fx = fx_long.pivot(index="date", columns="pair", values="close")
    fx.columns.name = None
    fx.index = pd.DatetimeIndex(fx.index)

    return prices, fx
