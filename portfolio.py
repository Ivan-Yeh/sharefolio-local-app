from asset import Asset
import json
import os
import pandas as pd
import yfinance as yf
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from configs import SUPPORTED_FX_PAIRS
from paths import APP_DATA_DIR
from portfolio_group import PortfolioGroup


class Portfolio:
    def __init__(
        self,
        trades: pd.DataFrame,
        dividends: pd.DataFrame,
        base_currency: str,
        historical_prices: pd.DataFrame | None = None,
        historical_fx: pd.DataFrame | None = None,
    ):
        self.trades: pd.DataFrame = trades
        self.dividends: pd.DataFrame = dividends
        self.assets_base_currency: dict[str, Asset] = {}
        self.assets_original_currency: dict[str, Asset] = {}
        self.inception_date: pd.Timestamp = self.trades.index.min()
        self.base_currency: str = base_currency

        self.historical_prices: pd.DataFrame = pd.DataFrame()
        self.historical_fx: pd.DataFrame = pd.DataFrame()

        self.trades_base: pd.DataFrame = pd.DataFrame()
        self.dividends_base: pd.DataFrame = pd.DataFrame()

        self.asset_names: dict[str: str] = dict()
        self.asset_names_file: Path = APP_DATA_DIR / "asset_names.json"
        self.asset_names_lock = Lock()
        self.total_group_base_currency: PortfolioGroup = None
        self.group_by_exchanges: dict[str, PortfolioGroup] = {}

        self.load_asset_names_cache()

        if historical_prices is not None and historical_fx is not None:
            self.historical_prices = historical_prices
            self.historical_fx = historical_fx
        else:
            self.fetch_historical_price_data()
        self.convert_trades_dividends_to_base_currency()
        self.load_assets_base_currency()
        self.load_assets_original_currency()
        self.group_assets()

    def load_asset_names_cache(self):
        if not self.asset_names_file.exists():
            return

        with self.asset_names_file.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, dict):
            self.asset_names = {str(k): str(v) for k, v in data.items()}

    def save_asset_names_cache(self):
        self.asset_names_file.parent.mkdir(parents=True, exist_ok=True)
        with self.asset_names_file.open("w", encoding="utf-8") as file:
            json.dump(self.asset_names, file, ensure_ascii=True, indent=2, sort_keys=True)


    def fetch_historical_price_data(self):
        all_symbols = sorted(set(self.trades["ticker"] + "." + self.trades["exchange"])) 
        yf_symbols = [s.replace(".US", "") for s in all_symbols] 
        historical_prices: pd.DataFrame = yf.download(yf_symbols, 
                                                      start=self.inception_date, 
                                                      multi_level_index=False, 
                                                      auto_adjust=False,
                                                      rounding=True)["Close"] 

        # if the start date of historical prices is after the inception date,
        if historical_prices.index.min() > self.inception_date:
            historical_prices = historical_prices.reindex(
                pd.date_range(start=self.inception_date, 
                              end=historical_prices.index.max(), 
                              freq='D'),
            )
        
        # fill the historical prices date to every day from inception date to today
        historical_prices = historical_prices.reindex(
            pd.date_range(start=self.inception_date, 
                          end=pd.Timestamp.today(), 
                          freq='D'),
            method='ffill'
        )
        historical_prices.ffill(inplace=True)

        historical_fx = yf.download(SUPPORTED_FX_PAIRS,
                                    start=self.inception_date,
                                    multi_level_index=False,
                                    auto_adjust=False,
                                    rounding=True)["Close"]

        historical_fx = historical_fx.reindex(
            pd.date_range(start=self.inception_date, 
                          end=pd.Timestamp.today(), 
                          freq='D'),
            method='ffill'
        )
        historical_fx.ffill(inplace=True)

        self.historical_prices = historical_prices
        self.historical_fx = historical_fx


    def _fill_fx_from_historical(self, df: pd.DataFrame) -> None:
        """Fill NaN fx values in-place using merged historical FX columns.
        fx convention: how many asset-currency units per 1 base-currency unit (e.g. AUDUSD for USD assets)."""
        for currency in df["currency"].unique():
            mask = df["currency"] == currency
            null_fx = mask & df["fx"].isna()
            if not null_fx.any():
                continue
            if currency == self.base_currency:
                df.loc[null_fx, "fx"] = 1.0
            else:
                fx_pair = f"{self.base_currency}{currency}=X"
                if fx_pair in df.columns:
                    df.loc[null_fx, "fx"] = df.loc[null_fx, fx_pair]

    def convert_trades_dividends_to_base_currency(self) -> float:
        # match the historical_fx to the trades and dividends based on date
        trades_base: pd.DataFrame = self.trades.merge(self.historical_fx, left_index=True, right_index=True, how='left')
        dividends_base: pd.DataFrame = self.dividends.merge(self.historical_fx, left_index=True, right_index=True, how='left')

        # fill NaN fx with historical rates; also propagate back to raw DataFrames
        self._fill_fx_from_historical(trades_base)
        self._fill_fx_from_historical(dividends_base)
        self.trades["fx"] = trades_base["fx"].values
        if not self.dividends.empty:
            self.dividends["fx"] = dividends_base["fx"].values

        def _resolve_fx(row: pd.Series, currency: str) -> float:
            """Return FX rate (asset-currency per base-currency, e.g. AUDUSD for USD assets).
            Use row['fx'] if provided, else fall back to historical data."""
            provided = row.get("fx")
            if provided is not None and pd.notna(provided) and float(provided) > 0:
                return float(provided)
            fx_pair = f"{self.base_currency}{currency}=X"
            if fx_pair in self.historical_fx.columns:
                return float(row[fx_pair])
            raise ValueError(f"No FX rate for {fx_pair} on {row.name}: neither 'fx' column nor historical data available")

        prices_in_base_currency = []
        fees_in_base_currency = []
        for _, row in trades_base.iterrows():
            if row["currency"] == self.base_currency:
                prices_in_base_currency.append(row["price"])
                fees_in_base_currency.append(row["fees"])
            else:
                fx_rate = _resolve_fx(row, row["currency"])
                prices_in_base_currency.append(row["price"] / fx_rate)
                fees_in_base_currency.append(row["fees"] / fx_rate)
        trades_base["price"] = prices_in_base_currency
        trades_base["fees"] = fees_in_base_currency

        amounts_in_base_currency = []
        tax_credit_in_base_currency = []
        for _, row in dividends_base.iterrows():
            if row["currency"] == self.base_currency:
                amounts_in_base_currency.append(row["amount"])
                tax_credit_in_base_currency.append(row["tax_credit"])
            else:
                fx_rate = _resolve_fx(row, row["currency"])
                amounts_in_base_currency.append(row["amount"] / fx_rate)
                tax_credit_in_base_currency.append(row["tax_credit"] / fx_rate)
        dividends_base["amount"] = amounts_in_base_currency
        dividends_base["tax_credit"] = tax_credit_in_base_currency
        self.trades_base = trades_base
        self.dividends_base = dividends_base


    def load_assets_original_currency(self): 
        all_symbols = sorted(set(self.trades["ticker"] + "." + self.trades["exchange"])) 
        yf_symbols = [s.replace(".US", "") for s in all_symbols] 
        symbol_pairs = list(zip(all_symbols, yf_symbols)) 
        def build_asset(full_symbol: str, yf_symbol: str):
            ticker, exchange = full_symbol.split(".")
            asset_trades = self.trades[(self.trades["ticker"] + "." + self.trades["exchange"]) == full_symbol]
            asset_dividends = self.dividends[(self.dividends["ticker"] + "." + self.dividends["exchange"]) == full_symbol]
            asset_historical_prices = self.historical_prices[yf_symbol]

            with self.asset_names_lock:
                cached_name = self.asset_names.get(yf_symbol)

            if cached_name is not None:
                asset_name = cached_name
            else:
                lookup_info = yf.Lookup(yf_symbol).get_all(1).to_dict()
                asset_name = lookup_info.get('shortName', {}).get(yf_symbol, yf_symbol)
                with self.asset_names_lock:
                    self.asset_names[yf_symbol] = asset_name

            return full_symbol, Asset(ticker=ticker,
                                      exchange=exchange,
                                      name=asset_name,
                                      currency=asset_trades["currency"].iloc[0],
                                      trades=asset_trades,
                                      dividends=asset_dividends,
                                      historical_prices=asset_historical_prices,
                                      historical_fx=self.historical_fx)

        max_workers = os.cpu_count() - 1
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(build_asset, full_symbol, yf_symbol) for full_symbol, yf_symbol in symbol_pairs]
            for future in tqdm(as_completed(futures), desc="Loading assets (original currency)", total=len(futures)):
                full_symbol, asset = future.result()
                self.assets_original_currency[full_symbol] = asset

        self.save_asset_names_cache()


    def load_assets_base_currency(self): 
        trades_base: pd.DataFrame = self.trades_base.copy()
        dividends_base: pd.DataFrame = self.dividends_base.copy()

        all_symbols = sorted(set(trades_base["ticker"] + "." + trades_base["exchange"])) 
        yf_symbols = [s.replace(".US", "") for s in all_symbols] 
        symbol_pairs = list(zip(all_symbols, yf_symbols)) 
        def build_asset(full_symbol: str, yf_symbol: str):
            ticker, exchange = full_symbol.split(".")
            asset_trades = trades_base[(trades_base["ticker"] + "." + trades_base["exchange"]) == full_symbol]
            asset_dividends = dividends_base[(dividends_base["ticker"] + "." + dividends_base["exchange"]) == full_symbol]
            currency = asset_trades["currency"].iloc[0]

            asset_historical_prices = self.historical_prices[yf_symbol]
            if currency != self.base_currency:
                fx_pair = f"{self.base_currency}{currency}=X"
                filtered_fx_df = asset_historical_prices.to_frame(name="close").merge(
                    self.historical_fx[[fx_pair]],
                    left_index=True,
                    right_index=True,
                    how='left',
                )
                # forward-fill any NaN FX rates (e.g. weekends / early dates with no data)
                filtered_fx_df[fx_pair] = filtered_fx_df[fx_pair].ffill().bfill()
                asset_historical_prices = pd.Series(
                    filtered_fx_df["close"] / filtered_fx_df[fx_pair],
                    index=filtered_fx_df.index,
                )
            
            with self.asset_names_lock:
                cached_name = self.asset_names.get(yf_symbol)

            if cached_name is not None:
                asset_name = cached_name
            else:
                lookup_info = yf.Lookup(yf_symbol).get_all(1).to_dict()
                asset_name = lookup_info.get('shortName', {}).get(yf_symbol, yf_symbol)
                with self.asset_names_lock:
                    self.asset_names[yf_symbol] = asset_name

            return full_symbol, Asset(ticker=ticker,
                                      exchange=exchange,
                                      name=asset_name,
                                      currency=currency,
                                      trades=asset_trades,
                                      dividends=asset_dividends,
                                      historical_prices=asset_historical_prices,
                                      historical_fx=self.historical_fx)

        max_workers = os.cpu_count() - 1
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(build_asset, full_symbol, yf_symbol)
                       for full_symbol, yf_symbol in symbol_pairs]
            for future in tqdm(as_completed(futures), 
                               desc="Loading assets (base currency)", 
                               total=len(futures)):
                full_symbol, asset = future.result()
                self.assets_base_currency[full_symbol] = asset
        self.save_asset_names_cache()


    def group_assets(self):
        self.total_group_base_currency = PortfolioGroup(group_name="Total", 
                                                        trades=self.trades_base,
                                                        dividends=self.dividends_base,
                                                        assets=self.assets_base_currency)
        all_exchanges = self.trades["exchange"].unique()
        for exchange in all_exchanges:
            exchange_trades = self.trades[self.trades["exchange"] == exchange]
            exchange_dividends = self.dividends[self.dividends["exchange"] == exchange]
            exchange_assets = {ticker: asset
                               for ticker, asset
                               in self.assets_original_currency.items()
                               if asset.exchange == exchange}
            self.group_by_exchanges[exchange] = PortfolioGroup(group_name=exchange, 
                                                               trades=exchange_trades, 
                                                               dividends=exchange_dividends, 
                                                               assets=exchange_assets)

    # def cumulative_performance(self, freq: str = "M") -> pd.DataFrame:
    #     return self.total_group_base_currency.cumulative_performance(freq=freq)

    # def open_position_performance(self, freq: str = "M") -> pd.DataFrame:
    #     return self.total_group_base_currency.open_position_performance(freq=freq)

    # def cumulative_performance_by_exchange(self, freq: str = "M") -> dict[str, pd.DataFrame]:
    #     return {
    #         exchange: group.cumulative_performance(freq=freq)
    #         for exchange, group in self.group_by_exchanges.items()
    #     }

    # def open_position_performance_by_exchange(self, freq: str = "M") -> dict[str, pd.DataFrame]:
    #     return {
    #         exchange: group.open_position_performance(freq=freq)
    #         for exchange, group in self.group_by_exchanges.items()
    #     }