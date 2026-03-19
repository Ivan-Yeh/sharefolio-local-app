from asset import Asset
import pandas as pd


class PortfolioGroup:
    def __init__(self, group_name: str, trades: pd.DataFrame, dividends: pd.DataFrame, assets: dict[str, Asset]):
        self.group_name: str = group_name
        self.trades: pd.DataFrame = trades
        self.dividends: pd.DataFrame = dividends
        self.assets: dict[str, Asset] = assets
        self.daily_performance_df: pd.DataFrame = self.compute_historical_daily_performance()

    def compute_total_commitment_on_date(self, date: pd.Timestamp) -> float:
        relevant_trades = self.trades[self.trades.index <= date]
        relevant_dividends = self.dividends[self.dividends.index <= date]
        if relevant_trades.empty and relevant_dividends.empty:
            return 0.0

        events = []

        net_quantity = [-q if t == "SELL" else q 
                        for q, t 
                        in zip(relevant_trades["quantity"], relevant_trades["type"])]
        cost_adjusted_prices = [p + f/abs(q) if q > 0 else p - f/abs(q) 
                                for q, p, f 
                                in zip(net_quantity, relevant_trades["price"], relevant_trades["fees"])]

        for ts, q, p in zip(relevant_trades.index, net_quantity, cost_adjusted_prices):
            events.append((ts, 1, -q * p))

        for ts, row in relevant_dividends.iterrows():
            dividend_cashflow = row["amount"] - row.get("fees", 0.0)
            events.append((ts, 0, dividend_cashflow))

        events.sort(key=lambda event: (event[0], event[1]))

        balance = 0.0
        capital_committed = 0.0
        for _, _, cashflow in events:
            balance += cashflow
            if balance < 0:
                capital_committed += -balance
                balance = 0.0

        return capital_committed
    

    def compute_historical_daily_performance(self):
        group_performance_df = None
        for asset in self.assets.values():
            if group_performance_df is None:
                group_performance_df = asset.daily_performance_df.copy()
            else:
                group_performance_df = group_performance_df + asset.daily_performance_df

        if group_performance_df is None:
            return pd.DataFrame(
                columns=[
                    "market_value",
                    "commitment",
                    "cost_basis",
                    "realised_pnl",
                    "unrealised_pnl",
                    "dividends",
                    "tax_credit",
                    "total_return",
                ]
            )

        group_performance_df["commitment"] = group_performance_df.apply(lambda row: self.compute_total_commitment_on_date(row.name), axis=1)

        group_performance_df.drop(columns=["close", "holdings"], inplace=True)
        group_performance_df.fillna(0.0, inplace=True)
        
        return group_performance_df

    def _safe_pct(self, numerator: pd.Series, denominator: pd.Series) -> pd.Series:
        denominator_non_zero = denominator.where(denominator != 0)
        return (numerator / denominator_non_zero).fillna(0.0)

    def _period_change(self, cumulative_series: pd.Series) -> pd.Series:
        return cumulative_series.sub(cumulative_series.shift(1)).fillna(cumulative_series)

    def _periodic_snapshot(self, freq: str) -> pd.DataFrame:
        daily_df = self.daily_performance_df.sort_index().copy()
        if daily_df.empty:
            return daily_df

        periodic_df = daily_df.groupby(pd.Grouper(freq=freq)).tail(1).copy()
        periodic_df.sort_index(inplace=True)

        period_labels = periodic_df.index.to_period(freq).astype(str).to_series(index=periodic_df.index)
        period_labels.iloc[-1] = "to date"
        periodic_df.insert(0, "period", period_labels)
        return periodic_df

    def cumulative_performance(self, freq: str = "M") -> pd.DataFrame:
        periodic_df = self._periodic_snapshot(freq)
        if periodic_df.empty:
            return periodic_df

        base_cols = [
            "period",
            "market_value",
            "commitment",
            "cost_basis",
            "realised_pnl",
            "unrealised_pnl",
            "dividends",
            "tax_credit",
            "total_return",
        ]
        performance_df = periodic_df[base_cols].copy()

        performance_df["total_pnl"] = performance_df["realised_pnl"] + performance_df["unrealised_pnl"]
        performance_df["realised_pnl_pct"] = self._safe_pct(performance_df["realised_pnl"], performance_df["commitment"])
        performance_df["unrealised_pnl_pct"] = self._safe_pct(performance_df["unrealised_pnl"], performance_df["commitment"])
        performance_df["total_pnl_pct"] = self._safe_pct(performance_df["total_pnl"], performance_df["commitment"])

        total_dividends = performance_df["dividends"] + performance_df["tax_credit"]
        performance_df["total_dividends"] = total_dividends
        performance_df["total_dividends_pct"] = self._safe_pct(total_dividends, performance_df["commitment"])
        performance_df["total_return_pct"] = self._safe_pct(performance_df["total_return"], performance_df["commitment"])

        performance_df["period_realised_pnl"] = self._period_change(performance_df["realised_pnl"])
        performance_df["period_unrealised_pnl"] = self._period_change(performance_df["unrealised_pnl"])
        performance_df["period_total_pnl"] = self._period_change(performance_df["total_pnl"])
        performance_df["period_total_dividends"] = self._period_change(total_dividends)
        performance_df["period_total_return"] = self._period_change(performance_df["total_return"])

        performance_df["period_realised_pnl_pct"] = self._safe_pct(performance_df["period_realised_pnl"], performance_df["commitment"])
        performance_df["period_unrealised_pnl_pct"] = self._safe_pct(performance_df["period_unrealised_pnl"], performance_df["commitment"])
        performance_df["period_total_pnl_pct"] = self._safe_pct(performance_df["period_total_pnl"], performance_df["commitment"])
        performance_df["period_total_dividends_pct"] = self._safe_pct(performance_df["period_total_dividends"], performance_df["commitment"])
        performance_df["period_total_return_pct"] = self._safe_pct(performance_df["period_total_return"], performance_df["commitment"])
        return performance_df

    def open_position_performance(self, freq: str = "M") -> pd.DataFrame:
        periodic_df = self._periodic_snapshot(freq)
        if periodic_df.empty:
            return periodic_df

        base_cols = [
            "period",
            "market_value",
            "commitment",
            "cost_basis",
            "realised_pnl",
            "unrealised_pnl",
            "dividends",
            "tax_credit",
            "total_return",
        ]
        performance_df = periodic_df[base_cols].copy()

        total_dividends = performance_df["dividends"] + performance_df["tax_credit"]
        performance_df["open_return"] = performance_df["unrealised_pnl"] + total_dividends
        performance_df["unrealised_pnl_pct"] = self._safe_pct(performance_df["unrealised_pnl"], performance_df["commitment"])
        performance_df["total_dividends"] = total_dividends
        performance_df["total_dividends_pct"] = self._safe_pct(total_dividends, performance_df["commitment"])
        performance_df["open_return_pct"] = self._safe_pct(performance_df["open_return"], performance_df["commitment"])

        performance_df["period_unrealised_pnl"] = self._period_change(performance_df["unrealised_pnl"])
        performance_df["period_total_dividends"] = self._period_change(total_dividends)
        performance_df["period_open_return"] = self._period_change(performance_df["open_return"])

        performance_df["period_unrealised_pnl_pct"] = self._safe_pct(performance_df["period_unrealised_pnl"], performance_df["commitment"])
        performance_df["period_total_dividends_pct"] = self._safe_pct(performance_df["period_total_dividends"], performance_df["commitment"])
        performance_df["period_open_return_pct"] = self._safe_pct(performance_df["period_open_return"], performance_df["commitment"])
        return performance_df
    
