import io
import json
import locale as _locale
import os
import shutil
import subprocess
import sys
import threading
import time as _time
import types
from pathlib import Path

import pandas as pd
from flask import Flask, Response, flash, jsonify, redirect, render_template, request, url_for

import db
import price_cache as _price_cache
from asset import Asset
from configs import SUPPORTED_CURRENCIES, SUPPORTED_EXCHANGES
from i18n import LANGUAGE_NAMES, SUPPORTED_LANGUAGES, t as _t
from paths import APP_DATA_DIR
from portfolio import Portfolio

# ── Constants ────────────────────────────────────────────────────────────────

CONFIG_FILE = APP_DATA_DIR / "config.json"


def _migrate_legacy_data() -> None:
    """One-time migration: copy data from the old tmp/ directory to APP_DATA_DIR."""
    _legacy = Path(__file__).parent / "tmp"
    if not _legacy.is_dir():
        return
    for name in ("trades.csv", "incomes.csv", "config.json"):
        src = _legacy / name
        dst = APP_DATA_DIR / name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)


_migrate_legacy_data()
try:
    db.init_db()
except Exception as _db_init_err:
    import logging as _logging
    _logging.getLogger(__name__).error("db.init_db() failed: %s", _db_init_err, exc_info=True)


def _detect_default_currency() -> str:
    try:
        loc = (_locale.getdefaultlocale()[0] or "").upper()
        if "_AU" in loc:
            return "AUD"
        if "_CA" in loc:
            return "CAD"
        if "_TW" in loc or loc.startswith("ZH_HANT"):
            return "TWD"
    except Exception:
        pass
    try:
        tz = _time.tzname[0].upper()
        if tz in ("AEST", "AEDT", "ACST", "ACDT", "AWST"):
            return "AUD"
    except Exception:
        pass
    return "USD"


_DEFAULT_BASE_CURRENCY: str = _detect_default_currency()


def _detect_default_language() -> str:
    try:
        loc = (_locale.getdefaultlocale()[0] or "").upper()
        if "ZH_TW" in loc or "ZH_HANT" in loc or "ZH_HK" in loc:
            return "zh_Hant"
    except Exception:
        pass
    return "en"


_DEFAULT_LANGUAGE: str = _detect_default_language()


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        with CONFIG_FILE.open() as f:
            return json.load(f)
    return {}


def _save_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_FILE.open("w") as f:
        json.dump(cfg, f, indent=2)


_config: dict = _load_config()
BASE_CURRENCY: str = _config.get("base_currency", _DEFAULT_BASE_CURRENCY)
LANGUAGE: str = _config.get("language", _DEFAULT_LANGUAGE)
if LANGUAGE not in SUPPORTED_LANGUAGES:
    LANGUAGE = "en"

CURRENCY_ICONS: dict[str, str] = {
    "AUD": "🇦🇺",
    "USD": "🇺🇸",
    "CAD": "🇨🇦",
    "TWD": "🇹🇼",
}
EXCHANGE_ICONS: dict[str, str] = {
    "US": "🇺🇸",
    "AX": "🇦🇺",
    "TW": "🇹🇼",
    "TWO": "🇹🇼",
    "TO": "🇨🇦",
}
EXCHANGE_NAMES: dict[str, str] = {
    "US": "NYSE / NASDAQ (US)",
    "AX": "ASX (Australia)",
    "TW": "TWSE (Taiwan)",
    "TWO": "OTC Market (Taiwan)",
    "TO": "TSX (Canada)",
}
FREQ_MAP: dict[str, str] = {
    "monthly": "ME",
    "quarterly": "QE",
    "semi-annual": "6ME",
    "annual": "YE",
}
EXCHANGE_CURRENCIES: dict[str, str] = {
    "US": "USD",
    "AX": "AUD",
    "TW": "TWD",
    "TWO": "TWD",
    "TO": "CAD",
}

# ── App setup ─────────────────────────────────────────────────────────────────

if getattr(sys, "frozen", False):
    _base = Path(sys._MEIPASS)
    app = Flask(__name__,
                template_folder=str(_base / "templates"),
                static_folder=str(_base / "static"))
else:
    app = Flask(__name__)

app.secret_key = "sharefolio-local-secret"


@app.context_processor
def _inject_i18n():
    html_lang = "zh-Hant" if LANGUAGE == "zh_Hant" else "en"
    return {
        "t": lambda key, **kw: _t(key, LANGUAGE, **kw),
        "language": LANGUAGE,
        "html_lang": html_lang,
        "supported_languages": SUPPORTED_LANGUAGES,
        "language_names": LANGUAGE_NAMES,
    }

# Injected into every HTML response at document-start so it runs before any
# page script — the only reliable way to intercept window.open in pywebview.
_PYWEBVIEW_GUARD_JS = """<script>
(function () {
    var _origOpen = window.open;
    var _origin   = window.location.origin;

    function _isExternal(url) {
        try { return new URL(url, window.location.href).origin !== _origin; }
        catch (e) { return false; }
    }
    function _openExternal(url) {
        if (window.pywebview && window.pywebview.api) {
            window.pywebview.api.open_external(url);
        }
    }

    // Override window.open — intercept before any page script can call it
    window.open = function (url, target, features) {
        if (!window.pywebview) return _origOpen.call(window, url, target, features);
        if (!url || url === '' || url === 'about:blank') return window;
        if (_isExternal(url)) { _openExternal(url); return window; }
        window.location.href = url;
        return window;
    };

    // Intercept target="_blank" links (capture phase = fires before any handler)
    document.addEventListener('click', function (e) {
        if (!window.pywebview) return;
        var a = e.target.closest('a[target="_blank"]');
        if (!a || !a.href) return;
        e.preventDefault(); e.stopPropagation();
        if (_isExternal(a.href)) { _openExternal(a.href); }
        else { window.location.href = a.href; }
    }, true);

    // Wire up Save-CSV buttons after DOM is ready
    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('[data-save-csv]').forEach(function (btn) {
            btn.addEventListener('click', function (e) {
                e.preventDefault(); e.stopPropagation();
                if (window.pywebview && window.pywebview.api) {
                    window.pywebview.api.save_csv(btn.getAttribute('data-save-csv'));
                }
            });
        });
    });
})();
</script>"""


@app.after_request
def _inject_pywebview_guard(response):
    if "text/html" in (response.content_type or ""):
        html = response.get_data(as_text=True)
        # Insert immediately after <head> so the script runs at document start
        if "<head>" in html:
            html = html.replace("<head>", "<head>" + _PYWEBVIEW_GUARD_JS, 1)
            response.set_data(html)
    return response

# ── Portfolio singleton ───────────────────────────────────────────────────────

_portfolio: Portfolio | None = None
_portfolio_lock = threading.Lock()
_portfolio_built_at: float = 0.0


def _load_portfolio() -> "Portfolio | None":
    """Build a Portfolio from the SQLite database and cached price data.

    Returns None if there are no trades or the price cache isn't ready yet
    (caller should show the loading/fetching screen instead).
    """
    if not db.has_trades():
        return None
    if not _price_cache.is_ready():
        return None

    trades = db.read_trades().drop(columns=["id"], errors="ignore")
    trades["date"] = pd.to_datetime(trades["date"])
    trades.set_index("date", inplace=True)
    trades.sort_index(inplace=True)
    if trades.empty:
        return None

    dividends = db.read_incomes().drop(columns=["id"], errors="ignore")
    if dividends.empty:
        dividends = pd.DataFrame(
            columns=["ticker", "exchange", "currency", "fx",
                     "amount", "tax_credit", "fees", "note"],
            index=pd.DatetimeIndex([], name="date"),
        )
    else:
        dividends["date"] = pd.to_datetime(dividends["date"])
        dividends.set_index("date", inplace=True)
        dividends.sort_index(inplace=True)

    hist_prices, hist_fx = _price_cache.load()
    return Portfolio(trades, dividends, base_currency=BASE_CURRENCY,
                     historical_prices=hist_prices, historical_fx=hist_fx)


def get_portfolio() -> "Portfolio | None":
    global _portfolio, _portfolio_built_at
    with _portfolio_lock:
        needs_build = _portfolio is None or _price_cache.last_updated() > _portfolio_built_at
        current = _portfolio

    if needs_build:
        new_p = _load_portfolio()
        if new_p is not None:
            with _portfolio_lock:
                _portfolio = new_p
                _portfolio_built_at = _time.time()
            return new_p
        return current

    return current


def reload_portfolio() -> None:
    global _portfolio, _portfolio_built_at
    new_p = _load_portfolio()
    with _portfolio_lock:
        if new_p is not None:
            _portfolio = new_p
            _portfolio_built_at = _time.time()


def _safe_reload() -> None:
    """Reload portfolio from cache; swallow errors so writes are not rolled back."""
    try:
        reload_portfolio()
    except Exception as exc:
        app.logger.error("Portfolio reload failed: %s", exc, exc_info=True)


def _async_reload() -> None:
    """Fire a non-blocking portfolio rebuild from the cache. Returns immediately."""
    threading.Thread(target=_safe_reload, daemon=True).start()


def _async_fetch_and_reload() -> None:
    """Re-fetch price data to disk after trades/incomes are edited.

    Does not touch any in-memory object; the portfolio is rebuilt lazily on the
    next page navigation once the cache has been refreshed.
    """
    def _do():
        symbols, inception = _get_fetch_params()
        if symbols:
            try:
                _price_cache.fetch_and_save(symbols, inception)
            except Exception as exc:
                app.logger.error("Triggered price fetch failed: %s", exc, exc_info=True)
    threading.Thread(target=_do, daemon=True).start()


def _get_fetch_params() -> "tuple[list[str], pd.Timestamp] | tuple[None, None]":
    """Read trades from the DB to derive yfinance symbols and inception date."""
    try:
        trades = db.read_trades()
        if trades.empty:
            return None, None
        symbols = sorted(set(trades["ticker"] + "." + trades["exchange"]))
        inception = pd.to_datetime(trades["date"]).min()
        return symbols, inception
    except Exception:
        return None, None


def _background_fetch_loop() -> None:
    """Fetch price data from yfinance every 30 s and write to disk cache only.

    Never modifies any in-memory object. The portfolio is rebuilt lazily from
    the updated cache on the next page navigation.
    """
    while True:
        symbols, inception = _get_fetch_params()
        if symbols:
            try:
                _price_cache.fetch_and_save(symbols, inception)
            except Exception as exc:
                app.logger.error("Background price fetch failed: %s", exc, exc_info=True)
        _time.sleep(30)


# Start background price-fetch daemon — only in the actual server process, not the
# Werkzeug reloader monitor process (which never serves requests).
if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    threading.Thread(target=_background_fetch_loop, daemon=True).start()


# ── DB-backed helpers ─────────────────────────────────────────────────────────

def _read_trades() -> pd.DataFrame:
    df = db.read_trades()
    if df.empty:
        return pd.DataFrame(columns=["date", "date_display", "ticker", "exchange", "currency", "fx",
                                     "type", "quantity", "price", "fees", "note", "id"])
    parsed = pd.to_datetime(df["date"], format="mixed", dayfirst=False)
    df["date"] = parsed.dt.strftime("%Y-%m-%d")
    df["date_display"] = parsed.dt.strftime("%-d %b %Y")
    return df


def _write_trades(df: pd.DataFrame) -> None:
    out = df.drop(columns=["id", "date_display"], errors="ignore")
    db.write_trades(out)


def _read_incomes() -> pd.DataFrame:
    df = db.read_incomes()
    if df.empty:
        return pd.DataFrame(columns=["date", "date_display", "ticker", "exchange", "currency", "fx",
                                     "amount", "tax_credit", "fees", "note", "id", "imputation"])
    df["imputation"] = df["tax_credit"]
    parsed = pd.to_datetime(df["date"], format="mixed", dayfirst=False)
    df["date"] = parsed.dt.strftime("%Y-%m-%d")
    df["date_display"] = parsed.dt.strftime("%-d %b %Y")
    return df


def _write_incomes(df: pd.DataFrame) -> None:
    out = df.drop(columns=["id", "imputation", "date_display"], errors="ignore")
    db.write_incomes(out)


# ── Data helpers ──────────────────────────────────────────────────────────────

def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def _trim_active(df: pd.DataFrame) -> pd.DataFrame:
    """Trim a daily performance df to start from the first date with any trade activity."""
    active = df[df["cost_basis"] != 0]
    if active.empty:
        return df
    return df[df.index >= active.index[0]]


_TRADES_REQUIRED = {"date", "ticker", "exchange", "currency", "type", "quantity", "price", "fees"}
_INCOMES_REQUIRED = {"date", "ticker", "exchange", "currency", "amount", "tax_credit", "fees"}


def _validate_csv(content: str, required_cols: set[str]) -> list[str]:
    errors: list[str] = []
    try:
        df = pd.read_csv(io.StringIO(content))
    except Exception as e:
        return [f"Could not parse file as CSV: {e}"]
    missing = required_cols - set(df.columns)
    if missing:
        errors.append(f"Missing required columns: {', '.join(sorted(missing))}")
        return errors
    try:
        pd.to_datetime(df["date"])
    except Exception:
        errors.append("Column 'date' contains values that cannot be parsed as dates")
    for col in required_cols - {"date", "ticker", "exchange", "currency", "type"}:
        try:
            pd.to_numeric(df[col])
        except Exception:
            errors.append(f"Column '{col}' contains non-numeric values")
    return errors



def _build_history_data(group_or_asset, is_asset: bool = False) -> dict:
    result: dict = {}
    for period_name, freq in FREQ_MAP.items():
        perf = group_or_asset.cumulative_performance(freq=freq)
        if perf.empty:
            result[period_name] = {
                "labels": [], "capital_gains": [], "income": [],
                "total_return": [], "capital_gains_pct": [],
                "income_pct": [], "total_return_pct": [],
            }
            continue
        result[period_name] = {
            "labels": perf["period"].tolist(),
            "capital_gains": perf["period_total_pnl"].round(2).tolist(),
            "income": perf["period_total_dividends"].round(2).tolist(),
            "total_return": perf["period_total_return"].round(2).tolist(),
            "capital_gains_pct": perf["period_total_pnl_pct"].round(4).tolist(),
            "income_pct": perf["period_total_dividends_pct"].round(4).tolist(),
            "total_return_pct": perf["period_total_return_pct"].round(4).tolist(),
        }
    return result


def _make_asset_dto(
    base_asset: Asset,
    orig_asset: Asset,
) -> types.SimpleNamespace:
    """Build a flat namespace with all fields the templates need."""
    commitment_base = base_asset.commitment or 1.0
    commitment_orig = orig_asset.commitment or 1.0

    dto = types.SimpleNamespace(
        ticker=base_asset.ticker,
        exchange=base_asset.exchange,
        currency=orig_asset.currency,
        name=base_asset.name or base_asset.ticker,
        current_holdings=0.0 if abs(base_asset.holdings) < 1e-9 else base_asset.holdings,
        current_share_price=round(float(orig_asset.daily_performance_df["close"].iloc[-1]), 2),
        # base currency fields
        current_value_base_currency=round(base_asset.current_market_value, 2),
        cost_basis_base_currency=round(base_asset.current_cost_basis, 2),
        total_return_dollar_base_currency=round(base_asset.cumulative_total_return, 2),
        total_return_pct_base_currency=round(_safe_div(base_asset.cumulative_total_return, commitment_base), 4),
        total_capital_gains_dollar_base_currency=round(base_asset.total_pnl, 2),
        total_capital_gains_pct_base_currency=round(_safe_div(base_asset.total_pnl, commitment_base), 4),
        total_income_dollar_base_currency=round(base_asset.total_dividends, 2),
        total_income_pct_base_currency=round(_safe_div(base_asset.total_dividends, commitment_base), 4),
        # asset currency fields
        current_value_asset_currency=round(orig_asset.current_market_value, 2),
        cost_basis_asset_currency=round(orig_asset.current_cost_basis, 2),
        total_return_dollar_asset_currency=round(orig_asset.cumulative_total_return, 2),
        total_return_pct_asset_currency=round(_safe_div(orig_asset.cumulative_total_return, commitment_orig), 4),
        total_capital_gains_dollar_asset_currency=round(orig_asset.total_pnl, 2),
        total_capital_gains_pct_asset_currency=round(_safe_div(orig_asset.total_pnl, commitment_orig), 4),
        total_income_dollar_asset_currency=round(orig_asset.total_dividends, 2),
        total_income_pct_asset_currency=round(_safe_div(orig_asset.total_dividends, commitment_orig), 4),
    )
    return dto


# ── API ───────────────────────────────────────────────────────────────────────

@app.route("/api/cache-status")
def api_cache_status():
    return jsonify({
        "ready": _price_cache.is_ready(),
        "fetching": _price_cache.is_fetching(),
        "last_updated": _price_cache.last_updated(),
    })


@app.route("/api/trigger-fetch", methods=["POST"])
def api_trigger_fetch():
    """Start a background price fetch if one isn't already running."""
    if _price_cache.is_fetching():
        return jsonify({"started": False, "reason": "already fetching"})
    symbols, inception = _get_fetch_params()
    if not symbols:
        return jsonify({"started": False, "reason": "no trades"})
    _async_fetch_and_reload()
    return jsonify({"started": True})


def _require_portfolio():
    """Return (portfolio, None) or (None, Response).

    Shows the fetching page when the price cache isn't ready yet, and the
    no-data page when there are no transactions at all.
    """
    if not db.has_trades():
        return None, render_template("no_data.html")

    if not _price_cache.is_ready():
        next_url = request.path
        if request.query_string:
            next_url += "?" + request.query_string.decode()
        return None, render_template("fetching.html", next_url=next_url)

    p = get_portfolio()
    if p is None:
        return None, render_template("no_data.html")
    return p, None


# ── Error handlers ────────────────────────────────────────────────────────────

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_error(e):
    return render_template("500.html"), 500


# ── Home ──────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", year=2026)


# ── Portfolio ─────────────────────────────────────────────────────────────────

@app.route("/portfolio")
def portfolio():
    p, no_data = _require_portfolio()
    if no_data:
        return no_data
    total = p.total_group_base_currency
    daily = total.daily_performance_df

    last = daily.iloc[-1]
    commitment = float(last["commitment"]) or 1.0
    total_return = float(last["total_return"])
    total_pnl = float(last["realised_pnl"]) + float(last["unrealised_pnl"])
    total_income = float(last["dividends"]) + float(last["tax_credit"])

    # ── Base currency KPI ─────────────────────────────────────────────────────
    portfolio_total_value_base_currency = round(float(last["market_value"]), 2)
    portfolio_total_contribution_base_currency = round(commitment, 2)
    portfolio_total_return_dollar_base_currency = round(total_return, 2)
    portfolio_total_return_pct_base_currency = round(_safe_div(total_return, commitment), 4)
    portfolio_total_capital_gains_dollar_base_currency = round(total_pnl, 2)
    portfolio_total_capital_gains_pct_base_currency = round(_safe_div(total_pnl, commitment), 4)
    portfolio_total_income_dollar_base_currency = round(total_income, 2)
    portfolio_total_income_pct_base_currency = round(_safe_div(total_income, commitment), 4)

    # ── TWR chart (base) ─────────────────────────────────────────────────────
    daily_trimmed = _trim_active(daily)
    portfolioValueBase_dates = daily_trimmed.index.strftime("%Y-%m-%d").tolist()
    portfolioValueBase_values = (daily_trimmed["twr"] * 100).round(4).tolist()

    # ── Pie chart (base) ──────────────────────────────────────────────────────
    all_asset_tickers: list[str] = []
    asset_weights_base: list[float] = []
    open_base = {sym: a for sym, a in p.assets_base_currency.items() if a.holdings > 0}
    total_mv_base = sum(a.current_market_value for a in open_base.values())
    for sym, asset in sorted(open_base.items(), key=lambda x: x[1].current_market_value, reverse=True):
        all_asset_tickers.append(asset.ticker)
        asset_weights_base.append(round(_safe_div(asset.current_market_value, total_mv_base), 4))

    # ── Historical performance chart (base) ───────────────────────────────────
    portfolio_history_base_data = _build_history_data(total)

    # ── Asset currency (per exchange) ─────────────────────────────────────────
    exchanges_in_portfolio = sorted(p.group_by_exchanges.keys())

    portfolio_total_contribution_asset_currencies: dict[str, float] = {}
    portfolio_total_value_asset_currencies: dict[str, float] = {}
    portfolio_total_return_dollar_asset_currencies: dict[str, float] = {}
    portfolio_total_return_pct_asset_currencies: dict[str, float] = {}
    portfolio_total_capital_gains_dollar_asset_currencies: dict[str, float] = {}
    portfolio_total_capital_gains_pct_asset_currencies: dict[str, float] = {}
    portfolio_total_income_dollar_asset_currencies: dict[str, float] = {}
    portfolio_total_income_pct_asset_currencies: dict[str, float] = {}
    portfolioValueAsset_dates: dict[str, list] = {}
    portfolioValueAsset_values: dict[str, list] = {}
    all_asset_tickers_asset_currencies: dict[str, list] = {}
    asset_weights_asset: dict[str, list] = {}
    portfolio_history_asset_currency_data: dict[str, dict] = {}

    for exch in exchanges_in_portfolio:
        group = p.group_by_exchanges[exch]
        g_daily = group.daily_performance_df
        if g_daily.empty:
            continue
        g_last = g_daily.iloc[-1]
        g_commit = float(g_last["commitment"]) or 1.0
        g_return = float(g_last["total_return"])
        g_pnl = float(g_last["realised_pnl"]) + float(g_last["unrealised_pnl"])
        g_income = float(g_last["dividends"]) + float(g_last["tax_credit"])

        portfolio_total_contribution_asset_currencies[exch] = round(g_commit, 2)
        portfolio_total_value_asset_currencies[exch] = round(float(g_last["market_value"]), 2)
        portfolio_total_return_dollar_asset_currencies[exch] = round(g_return, 2)
        portfolio_total_return_pct_asset_currencies[exch] = round(_safe_div(g_return, g_commit), 4)
        portfolio_total_capital_gains_dollar_asset_currencies[exch] = round(g_pnl, 2)
        portfolio_total_capital_gains_pct_asset_currencies[exch] = round(_safe_div(g_pnl, g_commit), 4)
        portfolio_total_income_dollar_asset_currencies[exch] = round(g_income, 2)
        portfolio_total_income_pct_asset_currencies[exch] = round(_safe_div(g_income, g_commit), 4)

        g_daily_trimmed = _trim_active(g_daily)
        portfolioValueAsset_dates[exch] = g_daily_trimmed.index.strftime("%Y-%m-%d").tolist()
        portfolioValueAsset_values[exch] = (g_daily_trimmed["twr"] * 100).round(4).tolist()

        # Pie chart for this exchange
        open_orig = {sym: a for sym, a in p.assets_original_currency.items()
                     if a.exchange == exch and a.holdings > 0}
        total_mv_orig = sum(a.current_market_value for a in open_orig.values())
        tickers_exch: list[str] = []
        weights_exch: list[float] = []
        for sym, asset in sorted(open_orig.items(), key=lambda x: x[1].current_market_value, reverse=True):
            tickers_exch.append(asset.ticker)
            weights_exch.append(round(_safe_div(asset.current_market_value, total_mv_orig), 4))
        all_asset_tickers_asset_currencies[exch] = tickers_exch
        asset_weights_asset[exch] = weights_exch

        portfolio_history_asset_currency_data[exch] = _build_history_data(group)

    return render_template(
        "portfolio.html",
        base_currency=BASE_CURRENCY,
        # KPI base
        portfolio_total_value_base_currency=portfolio_total_value_base_currency,
        portfolio_total_contribution_base_currency=portfolio_total_contribution_base_currency,
        portfolio_total_return_dollar_base_currency=portfolio_total_return_dollar_base_currency,
        portfolio_total_return_pct_base_currency=portfolio_total_return_pct_base_currency,
        portfolio_total_capital_gains_dollar_base_currency=portfolio_total_capital_gains_dollar_base_currency,
        portfolio_total_capital_gains_pct_base_currency=portfolio_total_capital_gains_pct_base_currency,
        portfolio_total_income_dollar_base_currency=portfolio_total_income_dollar_base_currency,
        portfolio_total_income_pct_base_currency=portfolio_total_income_pct_base_currency,
        # Charts base
        portfolioValueBase_dates=portfolioValueBase_dates,
        portfolioValueBase_values=portfolioValueBase_values,
        all_asset_tickers=all_asset_tickers,
        asset_weights_base=asset_weights_base,
        portfolio_history_base_data=portfolio_history_base_data,
        # Asset currency (by exchange, keyed as 'currencies' in template)
        currencies=exchanges_in_portfolio,
        portfolio_total_contribution_asset_currencies=portfolio_total_contribution_asset_currencies,
        portfolio_total_value_asset_currencies=portfolio_total_value_asset_currencies,
        portfolio_total_return_dollar_asset_currencies=portfolio_total_return_dollar_asset_currencies,
        portfolio_total_return_pct_asset_currencies=portfolio_total_return_pct_asset_currencies,
        portfolio_total_capital_gains_dollar_asset_currencies=portfolio_total_capital_gains_dollar_asset_currencies,
        portfolio_total_capital_gains_pct_asset_currencies=portfolio_total_capital_gains_pct_asset_currencies,
        portfolio_total_income_dollar_asset_currencies=portfolio_total_income_dollar_asset_currencies,
        portfolio_total_income_pct_asset_currencies=portfolio_total_income_pct_asset_currencies,
        portfolioValueAsset_dates=portfolioValueAsset_dates,
        portfolioValueAsset_values=portfolioValueAsset_values,
        all_asset_tickers_asset_currencies=all_asset_tickers_asset_currencies,
        asset_weights_asset=asset_weights_asset,
        portfolio_history_asset_currency_data=portfolio_history_asset_currency_data,
    )


# ── Holdings ──────────────────────────────────────────────────────────────────

@app.route("/holdings")
def holdings():
    p, no_data = _require_portfolio()
    if no_data:
        return no_data
    all_symbols = sorted(p.assets_base_currency.keys())

    asset_list = []
    for sym in all_symbols:
        base_a = p.assets_base_currency[sym]
        orig_a = p.assets_original_currency[sym]
        asset_list.append(_make_asset_dto(base_a, orig_a))

    exchanges_in_portfolio = sorted(p.group_by_exchanges.keys())
    portfolio_total_contribution_asset_currencies = {
        exch: round(float(p.group_by_exchanges[exch].daily_performance_df.iloc[-1]["commitment"]), 2)
        for exch in exchanges_in_portfolio
        if not p.group_by_exchanges[exch].daily_performance_df.empty
    }

    return render_template(
        "holdings.html",
        base_currency=BASE_CURRENCY,
        assets=asset_list,
        currencies=exchanges_in_portfolio,
        portfolio_total_contribution_asset_currencies=portfolio_total_contribution_asset_currencies,
        currency_icon=CURRENCY_ICONS,
        currency_icons=CURRENCY_ICONS,
        exchange_currencies=EXCHANGE_CURRENCIES,
    )


# ── Asset detail ──────────────────────────────────────────────────────────────

@app.route("/holdings/<ticker>")
def holding_detail(ticker: str):
    p, no_data = _require_portfolio()
    if no_data:
        return no_data

    # Find the matching symbol (ticker may appear in multiple exchanges)
    base_sym = next((s for s in p.assets_base_currency if s.startswith(ticker + ".")), None)
    if base_sym is None:
        return render_template("404.html"), 404

    base_a = p.assets_base_currency[base_sym]
    orig_a = p.assets_original_currency[base_sym]
    dto = _make_asset_dto(base_a, orig_a)

    # Historical performance data per tab
    asset_history_base_data = _build_history_data(base_a)
    asset_history_asset_data = _build_history_data(orig_a)

    # Trades and incomes for this asset
    trades_df = _read_trades()
    incomes_df = _read_incomes()
    asset_trades = trades_df[trades_df["ticker"] == ticker].sort_values("date", ascending=False).to_dict("records")
    asset_incomes = incomes_df[incomes_df["ticker"] == ticker].sort_values("date", ascending=False).to_dict("records")

    return render_template(
        "holding_details.html",
        ticker=ticker,
        base_currency=BASE_CURRENCY,
        asset_currency=orig_a.currency,
        currency_icon=CURRENCY_ICONS.get(orig_a.currency, ""),
        exchange_icon=EXCHANGE_ICONS.get(orig_a.exchange, ""),
        asset=dto,
        trades=asset_trades,
        incomes=asset_incomes,
        asset_history_base_data=asset_history_base_data,
        asset_history_asset_data=asset_history_asset_data,
        exchange_names=EXCHANGE_NAMES,
        currency_icons=CURRENCY_ICONS,
        currency=orig_a.currency,
    )


# ── Trades ────────────────────────────────────────────────────────────────────

@app.route("/trades")
def trades():
    keyword = request.args.get("keyword", "").strip().lower()
    df = _read_trades()
    if keyword:
        mask = df.apply(lambda r: keyword in str(r).lower(), axis=1)
        df = df[mask]
    records = df.sort_values("date", ascending=False).to_dict("records")
    return render_template(
        "trades.html",
        trades=records,
        exchange_names=EXCHANGE_NAMES,
        currency_icons=CURRENCY_ICONS,
    )


@app.route("/addtrade", methods=["GET", "POST"])
def add_trade():
    if request.method == "POST":
        df = _read_trades()
        fx_val = request.form.get("fx", "").strip()
        new_row = {
            "date": request.form["date"],
            "ticker": request.form["ticker"].strip().upper(),
            "exchange": request.form["exchange"],
            "currency": request.form["currency"],
            "fx": float(fx_val) if fx_val else None,
            "type": request.form["type"],
            "quantity": float(request.form["quantity"]),
            "price": float(request.form["price"]),
            "fees": float(request.form["fees"]),
            "note": request.form.get("note", "").strip(),
        }
        df = pd.concat([df.drop(columns=["id"]), pd.DataFrame([new_row])], ignore_index=True)
        _write_trades(df)
        _async_fetch_and_reload()
        flash("Trade added successfully.")
        return redirect(url_for("trades"))

    return render_template(
        "add_trade.html",
        currencies=SUPPORTED_CURRENCIES,
        exchanges=SUPPORTED_EXCHANGES,
        exchange_names=EXCHANGE_NAMES,
        currency_icons=CURRENCY_ICONS,
    )


@app.route("/trades/edit/<int:trade_id>", methods=["GET", "POST"])
def edit_trade(trade_id: int):
    df = _read_trades()
    if trade_id not in df["id"].values:
        return render_template("404.html"), 404
    row = df[df["id"] == trade_id].iloc[0].to_dict()

    if request.method == "POST":
        fx_val = request.form.get("fx", "").strip()
        df.loc[df["id"] == trade_id, "date"] = request.form["date"]
        df.loc[df["id"] == trade_id, "ticker"] = request.form["ticker"].strip().upper()
        df.loc[df["id"] == trade_id, "exchange"] = request.form["exchange"]
        df.loc[df["id"] == trade_id, "currency"] = request.form["currency"]
        df.loc[df["id"] == trade_id, "fx"] = float(fx_val) if fx_val else None
        df.loc[df["id"] == trade_id, "type"] = request.form["type"]
        df.loc[df["id"] == trade_id, "quantity"] = float(request.form["quantity"])
        df.loc[df["id"] == trade_id, "price"] = float(request.form["price"])
        df.loc[df["id"] == trade_id, "fees"] = float(request.form["fees"])
        df.loc[df["id"] == trade_id, "note"] = request.form.get("note", "").strip()
        _write_trades(df)
        _async_fetch_and_reload()
        flash("Trade updated successfully.")
        return redirect(url_for("trades"))

    return render_template(
        "edit_trade.html",
        trade=row,
        currencies=SUPPORTED_CURRENCIES,
        exchanges=SUPPORTED_EXCHANGES,
        exchange_names=EXCHANGE_NAMES,
        currency_icons=CURRENCY_ICONS,
    )


@app.route("/trades/update/<int:trade_id>", methods=["POST"])
def update_trade(trade_id: int):
    return edit_trade(trade_id)


@app.route("/trades/delete/<int:trade_id>", methods=["POST"])
def delete_trade_route(trade_id: int):
    df = _read_trades()
    df = df[df["id"] != trade_id].drop(columns=["id"])
    _write_trades(df)
    _async_fetch_and_reload()
    flash("Trade deleted.")
    return redirect(url_for("trades"))


@app.route("/downloadtrades")
def download_trades():
    df = _read_trades().drop(columns=["id", "date_display"], errors="ignore")
    csv_str = df.to_csv(index=False)
    if request.args.get("format") == "data":
        return jsonify({"content": csv_str, "filename": "trades.csv"})
    return Response(
        csv_str,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=trades.csv"},
    )


@app.route("/uploadtrades", methods=["POST"])
def upload_trades():
    file = request.files.get("file")
    if not file or not file.filename.endswith(".csv"):
        flash("Please upload a valid CSV file.")
        return redirect(url_for("trades"))
    try:
        content = file.read().decode("utf-8")
    except Exception as e:
        return render_template("upload_error.html", filename=file.filename,
                               file_type="Trades", errors=[f"Could not read file: {e}"],
                               back_url=url_for("trades"))
    errors = _validate_csv(content, _TRADES_REQUIRED)
    if errors:
        return render_template("upload_error.html", filename=file.filename,
                               file_type="Trades", errors=errors,
                               back_url=url_for("trades"))
    df_up = pd.read_csv(io.StringIO(content))
    df_up["date"] = pd.to_datetime(df_up["date"], format="mixed", dayfirst=False).dt.strftime("%Y-%m-%d")
    db.write_trades(df_up)
    _async_fetch_and_reload()
    flash("Trades CSV uploaded successfully.")
    return redirect(url_for("trades"))


# ── Incomes ───────────────────────────────────────────────────────────────────

@app.route("/incomes")
def incomes():
    keyword = request.args.get("keyword", "").strip().lower()
    df = _read_incomes()
    if keyword:
        mask = df.apply(lambda r: keyword in str(r).lower(), axis=1)
        df = df[mask]
    records = df.sort_values("date", ascending=False).to_dict("records")
    return render_template(
        "incomes.html",
        incomes=records,
        exchange_names=EXCHANGE_NAMES,
        currency_icons=CURRENCY_ICONS,
    )


def _get_holdings_list() -> list[tuple[str, str, str]]:
    """Return (ticker, exchange, currency) tuples for all known assets."""
    trades_df = _read_trades()
    seen: set[tuple] = set()
    result: list[tuple] = []
    for _, row in trades_df.iterrows():
        key = (str(row["ticker"]), str(row["exchange"]), str(row["currency"]))
        if key not in seen:
            seen.add(key)
            result.append(key)
    return sorted(result)


@app.route("/addincome", methods=["GET", "POST"])
def add_income():
    holdings = _get_holdings_list()

    if request.method == "POST":
        holding_val = request.form["holding"]
        ticker, exchange, currency = holding_val.split("|")
        fx_val = request.form.get("fx", "").strip()
        add_drp = "add_drp" in request.form

        df = _read_incomes().drop(columns=["id", "imputation"])
        new_income = {
            "date": request.form["date"],
            "ticker": ticker.strip().upper(),
            "exchange": exchange,
            "currency": currency,
            "fx": float(fx_val) if fx_val else None,
            "amount": float(request.form["amount"]),
            "tax_credit": float(request.form["imputation"]),
            "fees": float(request.form["fees"]),
            "note": request.form.get("note", "").strip(),
        }
        df = pd.concat([df, pd.DataFrame([new_income])], ignore_index=True)
        _write_incomes(df)

        if add_drp:
            drp_fx = request.form.get("drp_fx", "").strip()
            trades_df = _read_trades().drop(columns=["id"])
            new_trade = {
                "date": request.form.get("drp_date", request.form["date"]),
                "ticker": ticker.strip().upper(),
                "exchange": exchange,
                "currency": currency,
                "fx": float(drp_fx) if drp_fx else None,
                "type": "BUY",
                "quantity": float(request.form["drp_quantity"]),
                "price": float(request.form["drp_price"]),
                "fees": float(request.form.get("drp_fees", 0)),
                "note": "DRP",
            }
            trades_df = pd.concat([trades_df, pd.DataFrame([new_trade])], ignore_index=True)
            _write_trades(trades_df)

        _async_fetch_and_reload()
        flash("Income added successfully.")
        return redirect(url_for("incomes"))

    return render_template(
        "add_income.html",
        holdings=holdings,
        exchange_names=EXCHANGE_NAMES,
        currency_icons=CURRENCY_ICONS,
    )


@app.route("/incomes/edit/<int:income_id>", methods=["GET", "POST"])
def edit_income(income_id: int):
    df = _read_incomes()
    if income_id not in df["id"].values:
        return render_template("404.html"), 404
    row = df[df["id"] == income_id].iloc[0].to_dict()
    holdings = _get_holdings_list()

    if request.method == "POST":
        holding_val = request.form["holding"]
        ticker, exchange, currency = holding_val.split("|")
        fx_val = request.form.get("fx", "").strip()
        df.loc[df["id"] == income_id, "date"] = request.form["date"]
        df.loc[df["id"] == income_id, "ticker"] = ticker.strip().upper()
        df.loc[df["id"] == income_id, "exchange"] = exchange
        df.loc[df["id"] == income_id, "currency"] = currency
        df.loc[df["id"] == income_id, "fx"] = float(fx_val) if fx_val else None
        df.loc[df["id"] == income_id, "amount"] = float(request.form["amount"])
        df.loc[df["id"] == income_id, "tax_credit"] = float(request.form["imputation"])
        df.loc[df["id"] == income_id, "fees"] = float(request.form["fees"])
        df.loc[df["id"] == income_id, "note"] = request.form.get("note", "").strip()
        _write_incomes(df)
        _async_fetch_and_reload()
        flash("Income updated successfully.")
        return redirect(url_for("incomes"))

    return render_template(
        "edit_income.html",
        income=row,
        holdings=holdings,
        exchange_names=EXCHANGE_NAMES,
        currency_icons=CURRENCY_ICONS,
    )


@app.route("/incomes/update/<int:income_id>", methods=["POST"])
def update_income(income_id: int):
    return edit_income(income_id)


@app.route("/incomes/delete/<int:income_id>", methods=["POST"])
def delete_income_route(income_id: int):
    df = _read_incomes().drop(columns=["imputation"])
    df = df[df["id"] != income_id].drop(columns=["id"])
    _write_incomes(df)
    _async_fetch_and_reload()
    flash("Income deleted.")
    return redirect(url_for("incomes"))


@app.route("/downloadincomes")
def download_incomes():
    df = _read_incomes().drop(columns=["id", "imputation", "date_display"], errors="ignore")
    csv_str = df.to_csv(index=False)
    if request.args.get("format") == "data":
        return jsonify({"content": csv_str, "filename": "incomes.csv"})
    return Response(
        csv_str,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=incomes.csv"},
    )


@app.route("/uploadincomes", methods=["POST"])
def upload_incomes():
    file = request.files.get("file")
    if not file or not file.filename.endswith(".csv"):
        flash("Please upload a valid CSV file.")
        return redirect(url_for("incomes"))
    try:
        content = file.read().decode("utf-8")
    except Exception as e:
        return render_template("upload_error.html", filename=file.filename,
                               file_type="Incomes", errors=[f"Could not read file: {e}"],
                               back_url=url_for("incomes"))
    errors = _validate_csv(content, _INCOMES_REQUIRED)
    if errors:
        return render_template("upload_error.html", filename=file.filename,
                               file_type="Incomes", errors=errors,
                               back_url=url_for("incomes"))
    df_up = pd.read_csv(io.StringIO(content))
    df_up["date"] = pd.to_datetime(df_up["date"], format="mixed", dayfirst=False).dt.strftime("%Y-%m-%d")
    if "tax_credit" not in df_up.columns and "imputation" in df_up.columns:
        df_up = df_up.rename(columns={"imputation": "tax_credit"})
    db.write_incomes(df_up)
    _async_fetch_and_reload()
    flash("Incomes CSV uploaded successfully.")
    return redirect(url_for("incomes"))


# ── Info / FAQ ────────────────────────────────────────────────────────────────

@app.route("/info")
def info():
    return render_template("info.html")


# ── Settings ──────────────────────────────────────────────────────────────────

@app.route("/settings", methods=["GET", "POST"])
def settings():
    global _config, BASE_CURRENCY, LANGUAGE
    if request.method == "POST":
        form_name = request.form.get("form_name", "base_currency")
        if form_name == "language":
            new_language = request.form.get("language", "").strip()
            if new_language in SUPPORTED_LANGUAGES:
                LANGUAGE = new_language
                _config["language"] = new_language
                _save_config(_config)
                flash(_t("settings.flash_language_changed", new_language,
                         language=LANGUAGE_NAMES[new_language]))
            else:
                flash(_t("settings.flash_language_invalid", LANGUAGE))
        else:
            new_currency = request.form.get("base_currency", "").strip()
            if new_currency in SUPPORTED_CURRENCIES:
                BASE_CURRENCY = new_currency
                _config["base_currency"] = new_currency
                _save_config(_config)
                _async_reload()
                flash(_t("settings.flash_currency_changed", LANGUAGE, currency=new_currency))
            else:
                flash(_t("settings.flash_currency_invalid", LANGUAGE))
        return redirect(url_for("settings"))
    return render_template(
        "settings.html",
        base_currency=BASE_CURRENCY,
        supported_currencies=SUPPORTED_CURRENCIES,
        app_data_dir=str(APP_DATA_DIR),
    )


@app.route("/open-data-folder")
def open_data_folder():
    subprocess.Popen(["open", str(APP_DATA_DIR)])
    return "", 204


# ── Stub routes referenced in templates ──────────────────────────────────────

# @app.route("/logout")
# def logout():
#     return redirect(url_for("index"))


# @app.route("/login")
# def login():
#     return redirect(url_for("portfolio"))


# @app.route("/signup")
# def signup():
#     return redirect(url_for("portfolio"))


# if __name__ == "__main__":
#     app.run(debug=False)
