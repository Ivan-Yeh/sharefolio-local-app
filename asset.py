import pandas as pd


class Asset:

    __slots__ = ["ticker", "exchange", "name", "currency", "trades", "dividends", "historical_prices", "historical_fx", "holdings", "daily_performance_df", "current_cost_basis", "current_market_value", "current_unrealised_pnl", "total_realised_pnl", "total_pnl", "total_dividends", "cumulative_total_return", "commitment"]

    def __init__(self, 
                 ticker: str, 
                 exchange: str, 
                 name: str,
                 currency: str,
                 trades: pd.DataFrame, 
                 dividends: pd.DataFrame,
                 historical_prices: pd.Series,
                 historical_fx: pd.Series):
        
        self.ticker: str = ticker
        self.exchange: str = exchange
        self.name: str = name
        self.currency: str = currency
        self.trades: pd.DataFrame = trades.sort_index(kind="stable").copy()
        self.dividends: pd.DataFrame = dividends.sort_index(kind="stable").copy()
        self.historical_prices: pd.DataFrame = historical_prices.to_frame(name="close")
        self.historical_fx: pd.DataFrame = historical_fx

        # compute holdings information
        self.holdings: float = float(self.trades[self.trades["type"] == "BUY"]["quantity"].sum() - 
                                     self.trades[self.trades["type"] == "SELL"]["quantity"].sum() + 
                                     self.trades[self.trades["type"] == "ADJUSTMENT"]["quantity"].sum())
        
        self.daily_performance_df: pd.DataFrame = self.compute_historical_daily_performance()

        # key info
        self.current_cost_basis: float = self.daily_performance_df["cost_basis"].iloc[-1].round(2)
        self.current_market_value: float = self.daily_performance_df["market_value"].iloc[-1].round(2)
        self.current_unrealised_pnl: float = self.daily_performance_df["unrealised_pnl"].iloc[-1].round(2)
        self.total_realised_pnl: float = self.daily_performance_df["realised_pnl"].iloc[-1].round(2)
        self.total_pnl = round(self.total_realised_pnl + self.current_unrealised_pnl, 2)
        self.total_dividends: float = self.daily_performance_df["dividends"].iloc[-1] + self.daily_performance_df["tax_credit"].iloc[-1].round(2)
        self.cumulative_total_return: float = self.daily_performance_df["total_return"].iloc[-1].round(2)
        self.commitment: float = self.daily_performance_df["commitment"].iloc[-1].round(2)
        

    def cost_basis_on_date(self, date: pd.Timestamp) -> tuple[float, float]:
        # fifo method to compute cost basis
        relevant_trades = self.trades[self.trades.index <= date]
        if relevant_trades.empty:
            return 0.0, 0.0
        
        net_quantity = [-q if t == "SELL" else q 
                        for q, t 
                        in zip(relevant_trades["quantity"], relevant_trades["type"])]
        
        # price + fee for buys; price - fee for sells, or adjustments
        cost_adjusted_prices = [p + f/abs(q) if q > 0 else p - f/abs(q) 
                                for q, p, f 
                                in zip(net_quantity, relevant_trades["price"], relevant_trades["fees"])]

        def compute_fifo_lots(qty, price) -> tuple[list[tuple[float, float]], float]:
            def sign(x):
                if x > 0:
                    return 1
                elif x < 0:
                    return -1
                else:
                    return 0
            lots = []
            realised_pnl = 0.0
            for i, (q, p) in enumerate(zip(qty, price)):
                if len(lots) == 0:
                    lots.append((q, p))
                    continue
                for j, (lot_q, lot_p) in enumerate(lots):
                    if sign(q) != sign(lot_q):
                        if abs(q) > abs(lot_q):
                            q += lot_q
                            lots[j] = (0, lot_p)
                            realised_pnl += abs(lot_q) * (p - lot_p) * sign(lot_q)
                        else:
                            lots[j] = (lot_q + q, lot_p)
                            realised_pnl += abs(q) * (p - lot_p) * sign(lot_q)
                            q = 0
                            break
                if q != 0:
                    lots.append((q, p))
            return lots, realised_pnl
        
        lots, realised_pnl = compute_fifo_lots(net_quantity, cost_adjusted_prices)
        cost_basis = sum(q * p for q, p in lots)
        return cost_basis, realised_pnl

    def holdings_on_date(self, date: pd.Timestamp) -> float:
        relevant_trades = self.trades[self.trades.index <= date]
        holdings = float(relevant_trades[relevant_trades["type"] == "BUY"]["quantity"].sum() - 
                         relevant_trades[relevant_trades["type"] == "SELL"]["quantity"].sum() + 
                         relevant_trades[relevant_trades["type"] == "ADJUSTMENT"]["quantity"].sum())
        return holdings
    
    def dividends_on_date(self, date: pd.Timestamp) -> float:
        relevant_dividends = self.dividends[self.dividends.index <= date]
        dividends = float(relevant_dividends["amount"].sum() +
                          relevant_dividends["fees"].sum()) 
        credits = float(relevant_dividends["tax_credit"].sum())
        return dividends, credits

    def capital_commitment_on_date(self, date: pd.Timestamp) -> float:
        relevant_trades = self.trades[self.trades.index <= date]
        relevant_dividends = self.dividends[self.dividends.index <= date]
        if relevant_trades.empty and relevant_dividends.empty:
            return 0.0

        events = []
        
        net_quantity = [-q if t == "SELL" else q 
                        for q, t 
                        in zip(relevant_trades["quantity"], relevant_trades["type"])]
        
        # price + fee for buys; price - fee for sells, or adjustments
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
        daily_df = self.historical_prices.copy()

        state_columns = [
            "holdings",
            "commitment",
            "cost_basis",
            "realised_pnl",
            "dividends",
            "tax_credit",
        ]
        if self.trades.empty and self.dividends.empty:
            for column in state_columns:
                daily_df[column] = 0.0
            daily_df["market_value"] = 0.0
            daily_df["unrealised_pnl"] = 0.0
            daily_df["total_return"] = 0.0
            return daily_df

        def sign(value: float) -> int:
            if value > 0:
                return 1
            if value < 0:
                return -1
            return 0

        events = []

        for ts, trade_type, quantity, price, fees in self.trades[["type", "quantity", "price", "fees"]].itertuples(index=True, name=None):
            signed_quantity = -quantity if trade_type == "SELL" else quantity
            adjusted_price = price + fees / abs(signed_quantity) if signed_quantity > 0 else price - fees / abs(signed_quantity)
            trade_cashflow = -signed_quantity * adjusted_price
            events.append((pd.Timestamp(ts).normalize(), 1, signed_quantity, adjusted_price, 0.0, 0.0, trade_cashflow))

        for ts, amount, tax_credit, fees in self.dividends[["amount", "tax_credit", "fees"]].itertuples(index=True, name=None):
            dividend_total = amount + fees
            dividend_cashflow = amount - fees
            events.append((pd.Timestamp(ts).normalize(), 0, 0.0, 0.0, dividend_total, tax_credit, dividend_cashflow))

        events.sort(key=lambda event: (event[0], event[1]))

        holdings = 0.0
        balance = 0.0
        capital_committed = 0.0
        cost_basis = 0.0
        realised_pnl = 0.0
        dividends = 0.0
        tax_credits = 0.0
        lots: list[tuple[float, float]] = []
        snapshots: dict[pd.Timestamp, tuple[float, float, float, float, float, float]] = {}

        for event_date, event_type, quantity, price, dividend_total, tax_credit, cashflow in events:
            if event_type == 0:
                dividends += dividend_total
                tax_credits += tax_credit
            else:
                holdings += quantity
                remaining_quantity = quantity

                for lot_index, (lot_quantity, lot_price) in enumerate(lots):
                    if remaining_quantity == 0:
                        break
                    if lot_quantity == 0 or sign(remaining_quantity) == sign(lot_quantity):
                        continue

                    closed_quantity = min(abs(remaining_quantity), abs(lot_quantity))
                    realised_pnl += closed_quantity * (price - lot_price) * sign(lot_quantity)
                    cost_basis -= sign(lot_quantity) * closed_quantity * lot_price

                    if abs(remaining_quantity) > abs(lot_quantity):
                        remaining_quantity += lot_quantity
                        lots[lot_index] = (0.0, lot_price)
                    else:
                        lots[lot_index] = (lot_quantity + remaining_quantity, lot_price)
                        remaining_quantity = 0.0
                        break

                if remaining_quantity != 0:
                    lots.append((remaining_quantity, price))
                    cost_basis += remaining_quantity * price

            balance += cashflow
            if balance < 0:
                capital_committed += -balance
                balance = 0.0

            snapshots[event_date] = (
                holdings,
                capital_committed,
                cost_basis,
                realised_pnl,
                dividends,
                tax_credits,
            )

        event_state_df = pd.DataFrame.from_dict(
            snapshots,
            orient="index",
            columns=state_columns,
        ).sort_index()

        daily_df = daily_df.join(event_state_df.reindex(daily_df.index, method="ffill"))
        daily_df[state_columns] = daily_df[state_columns].fillna(0.0)
        daily_df["market_value"] = daily_df["close"] * daily_df["holdings"]
        daily_df["unrealised_pnl"] = daily_df["market_value"] - daily_df["cost_basis"]
        daily_df["total_return"] = daily_df["realised_pnl"] + daily_df["unrealised_pnl"] + daily_df["dividends"] + daily_df["tax_credit"]
        return daily_df

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

        # periodic cumulative performance metrics
        base_cols = [
            "period",
            "holdings",
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

        # periodic performance metrics
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
            "holdings",
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


    def __repr__(self):
        return f"Asset({self.name=}, {self.ticker=}, {self.exchange=}, {self.currency=}, {self.holdings=})"