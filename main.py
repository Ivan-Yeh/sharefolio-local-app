import pandas as pd
from portfolio import Portfolio

def main():
    # Load trades and dividends data from CSV files
    trades = pd.read_csv('data/trades.csv', parse_dates=['date'])
    trades["note"] = trades["note"].fillna("")
    trades.set_index('date', inplace=True)
    trades.sort_index(inplace=True)
    dividends = pd.read_csv('data/incomes.csv', parse_dates=['date'])
    dividends["note"] = dividends["note"].fillna("")
    dividends.set_index('date', inplace=True)
    dividends.sort_index(inplace=True)


    # Create a Portfolio instance
    portfolio = Portfolio(trades, dividends, base_currency="AUD")
    symbol = "BHP.AX"
    # print(portfolio.assets_base_currency[symbol].compute_historical_daily_performance().tail(50).round(2))
    # print(portfolio.assets_base_currency[symbol].cumulative_performance("Y").tail(50).round(2))
    # print(portfolio.assets_base_currency[symbol].open_position_performance("Y").tail(50).round(2))
    print(portfolio.group_by_exchanges["US"].cumulative_performance("Y").tail(50).round(2))
    print(portfolio.total_group_base_currency.cumulative_performance("Y").tail(50).round(2))
main()