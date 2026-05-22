"""SQLite-backed storage for trades and incomes.

Replaces the previous trades.csv / incomes.csv flat files.
On first run, automatically imports existing CSV files if found in APP_DATA_DIR.
"""

import sqlite3

import pandas as pd

from paths import APP_DATA_DIR

DB_FILE = APP_DATA_DIR / "sharefolio.db"
_initialized = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    date     TEXT    NOT NULL,
    ticker   TEXT    NOT NULL,
    exchange TEXT    NOT NULL,
    currency TEXT    NOT NULL,
    fx       REAL,
    type     TEXT    NOT NULL,
    quantity REAL    NOT NULL,
    price    REAL    NOT NULL,
    fees     REAL    NOT NULL,
    note     TEXT    NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS incomes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT    NOT NULL,
    ticker     TEXT    NOT NULL,
    exchange   TEXT    NOT NULL,
    currency   TEXT    NOT NULL,
    fx         REAL,
    amount     REAL    NOT NULL,
    tax_credit REAL    NOT NULL,
    fees       REAL    NOT NULL,
    note       TEXT    NOT NULL DEFAULT ''
);
"""

_TRADES_COLS = ["date", "ticker", "exchange", "currency", "fx", "type", "quantity", "price", "fees", "note"]
_INCOMES_COLS = ["date", "ticker", "exchange", "currency", "fx", "amount", "tax_credit", "fees", "note"]
_TEXT_COLS = {"date", "ticker", "exchange", "currency", "type", "note"}


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Prepare a DataFrame for SQLite insertion.

    - Fills NaN in text columns with '' (satisfies NOT NULL TEXT constraints)
    - Converts NaN in numeric columns to None (stored as SQL NULL)
    """
    df = df.copy()
    for col in df.columns:
        if col in _TEXT_COLS:
            df[col] = df[col].fillna("")
        else:
            df[col] = df[col].where(df[col].notna(), other=None)
    return df


def _open() -> sqlite3.Connection:
    con = sqlite3.connect(DB_FILE)
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db() -> None:
    """Create tables (idempotent) and migrate from CSV if the tables are empty."""
    global _initialized
    if _initialized:
        return
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    con = _open()
    con.executescript(_SCHEMA)
    con.close()
    _migrate_from_csv()
    _initialized = True


def _migrate_from_csv() -> None:
    """One-time import of existing CSV files into the SQLite tables."""
    trades_csv = APP_DATA_DIR / "trades.csv"
    incomes_csv = APP_DATA_DIR / "incomes.csv"
    con = _open()
    try:
        if trades_csv.exists() and con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0:
            df = pd.read_csv(trades_csv)
            df["note"] = df["note"].fillna("")
            df[_TRADES_COLS].to_sql("trades", con, if_exists="append", index=False)

        if incomes_csv.exists() and con.execute("SELECT COUNT(*) FROM incomes").fetchone()[0] == 0:
            df = pd.read_csv(incomes_csv)
            df["note"] = df["note"].fillna("")
            if "tax_credit" not in df.columns and "imputation" in df.columns:
                df = df.rename(columns={"imputation": "tax_credit"})
            df[_INCOMES_COLS].to_sql("incomes", con, if_exists="append", index=False)
        con.commit()
    finally:
        con.close()


def has_trades() -> bool:
    """Return True if the trades table is non-empty. Safe to call before init_db()."""
    try:
        con = _open()
        try:
            return con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] > 0
        finally:
            con.close()
    except Exception:
        return False


def read_trades() -> pd.DataFrame:
    """Return all trades ordered by date. Includes the 'id' column."""
    init_db()
    con = _open()
    try:
        df = pd.read_sql("SELECT * FROM trades ORDER BY date", con)
    finally:
        con.close()
    if df.empty:
        return pd.DataFrame(columns=["id"] + _TRADES_COLS)
    df["note"] = df["note"].fillna("")
    df["fx"] = df["fx"].where(df["fx"].notna(), None)
    return df


def write_trades(df: pd.DataFrame) -> None:
    """Replace all trades atomically (DELETE + INSERT in one transaction)."""
    init_db()
    cols = [c for c in _TRADES_COLS if c in df.columns]
    rows = [tuple(row) for row in _clean(df[cols]).itertuples(index=False, name=None)]
    placeholders = ", ".join("?" * len(cols))
    col_list = ", ".join(cols)
    con = _open()
    try:
        con.execute("BEGIN EXCLUSIVE")
        con.execute("DELETE FROM trades")
        con.executemany(f"INSERT INTO trades ({col_list}) VALUES ({placeholders})", rows)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def read_incomes() -> pd.DataFrame:
    """Return all incomes ordered by date. Includes the 'id' column."""
    init_db()
    con = _open()
    try:
        df = pd.read_sql("SELECT * FROM incomes ORDER BY date", con)
    finally:
        con.close()
    if df.empty:
        return pd.DataFrame(columns=["id"] + _INCOMES_COLS)
    df["note"] = df["note"].fillna("")
    df["fx"] = df["fx"].where(df["fx"].notna(), None)
    return df


def write_incomes(df: pd.DataFrame) -> None:
    """Replace all incomes atomically (DELETE + INSERT in one transaction)."""
    init_db()
    cols = [c for c in _INCOMES_COLS if c in df.columns]
    rows = [tuple(row) for row in _clean(df[cols]).itertuples(index=False, name=None)]
    placeholders = ", ".join("?" * len(cols))
    col_list = ", ".join(cols)
    con = _open()
    try:
        con.execute("BEGIN EXCLUSIVE")
        con.execute("DELETE FROM incomes")
        con.executemany(f"INSERT INTO incomes ({col_list}) VALUES ({placeholders})", rows)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
