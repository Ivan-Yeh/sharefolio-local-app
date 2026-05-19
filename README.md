# sharefolio-local

## Product Plan

Build a cohesive portfolio web app UI using the existing templates visual language as the source of truth.

### Core Objective

Deliver five pages with clear UI and data contracts:
1. Portfolio
2. Holdings
3. Asset Detail
4. Transactions (merged trades + dividends)
5. Tax Tool

### Global Constraints

1. Use templates as the baseline for styles and layout patterns.
2. Preserve the existing app shell pattern: fixed header, side panel, content wrapper.
3. Reuse established components: tab toggle, KPI cards, tables, chart panels, and form controls.
4. Keep visual behavior consistent across desktop and mobile.
5. Include loading, empty, populated, and error states for each table and chart section.

### Shared UX Contracts

1. Currency mode toggle:
	- Base Currency mode
	- Asset Currency mode
2. Formatting:
	- Consistent currency formatting for values
	- Consistent percentage precision and sign handling
3. Interaction consistency:
	- Same button placement patterns for add/edit/delete/view actions
	- Same chart timeframe selector behavior across relevant pages

## 1) Portfolio Page

### Data Source Behavior

1. Base Currency mode uses `portfolio.total_group_base_currency`.
2. Asset Currency mode uses `portfolio.group_by_exchanges`.
3. In Asset Currency mode, each exchange is shown as a tab (one portfolio group per exchange).

### Required Sections

1. Top KPI cards:
	- Total contribution
	- Current market value with cost base
	- Total capital gains with percentage (realized + unrealized)
	- Total dividends with percentage
2. Charts row (2 columns):
	- Left: line chart for cumulative total return %
	- Right: pie chart for asset allocation
3. Historical performance row (2 columns):
	- Timeframe selector: monthly, quarterly, semi-annual, annual
	- Left: stacked bar chart for periodic return
	- Right: stacked bar chart for periodic cumulative return

## 2) Holdings Page

### Data Source Behavior

1. Base Currency mode displays assets from `portfolio.total_group_base_currency`.
2. Asset Currency mode displays assets from `portfolio.group_by_exchanges` with exchange tabs.

### Required Sections

1. Top section: Open positions
2. Bottom section: Closed positions

### Open Positions Table Columns

1. Ticker
2. Name
3. Currency
4. Holdings
5. Market value
6. Total capital gains %
7. Total dividends %
8. Total return %
9. View action

### Closed Positions Table Columns

1. Ticker
2. Name
3. Currency
4. Total capital gains %
5. Total dividends %
6. Total return %
7. View action

### Action Behavior

1. Every row includes a View button linking to the dedicated asset detail page.

## 3) Asset Detail Page

### Header Block

1. Name
2. Ticker
3. Exchange
4. Currency

### KPI Section

1. Market value with cost basis
2. Realized capital gains %
3. Unrealized capital gains %
4. Total dividends %
5. Total return %

### Historical Performance

1. Timeframe selector: monthly, quarterly, semi-annual, annual
2. Left: stacked bar (realised pnl, unrealised pnl, dividends, tax credit) chart for periodic return
3. Right: stacked bar chart (realised pnl, unrealised pnl, dividends, tax credit) for periodic cumulative return

## 4) Transactions Page (Merged)

### Scope Decision

Use one combined Transactions page that includes both trades and dividends.

### Top Controls

1. Add Trade button
2. Add Dividend button
3. Search bar
4. Quick filters:
	- All
	- Trades only
	- Dividends only
5. Upload file: pop-up form for dividend upload or transaction upload (warn the user that the uploaded files will overwrite the existing records)
6. Download File button: automatically save all trades and dividends CSVs in the default download folder.

### Unified List Row Schemas

1. Trade row fields:
	- date, ticker, exchange, currency, type, quantity, price, fees, note
2. Dividend row fields:
	- date, ticker, exchange, currency, amount, tax_credit, fees, note

### Row Actions

1. Edit
2. Delete

### Add/Edit Interaction

1. Use popup modal forms (not standalone page flow).
2. Dividend modal must include an optional dividend reinvestment section with trade-like entry fields.

## 5) Tax Tool Page

### Required Controls

1. Currency mode toggle (base/asset)
2. Date range selector

### Required Outputs

Display within selected date range:
1. Trades
2. Dividends
3. Tax credits
4. Realized capital gains

## Implementation Sequence

1. Build shared UI foundation from templates patterns (tabs, KPI cards, table patterns, chart containers, modal patterns).
2. Implement Portfolio page first as the reference implementation.
3. Implement Holdings page with open/closed partition and consistent action columns.
4. Implement Asset Detail page using the same KPI/chart system.
5. Implement merged Transactions page with modal-based add/edit flows.
6. Implement Tax Tool page filters and outputs.
7. Run responsive and state-handling QA across all pages.

## Definition of Done

1. Every page and field listed above is present.
2. Currency mode behavior is consistent on applicable pages.
3. Exchange-tab behavior works in asset-currency mode where applicable.
4. Required chart sections and timeframe selectors are implemented.
5. Transactions page supports unified list, quick filters, and modal add/edit actions.
6. Tax Tool supports date-range filtering and required outputs.
7. Styling stays consistent with templates baseline, with no parallel design language introduced.

## Files and Distributions
- Use FLASK and HTML to handle all front and back ends
- Use webview to make it a local app
- To ensure that all transaction records are persistent, store the records in relevant DBs on the disk, so even when the app is removed/reinstalled, the records persist in the dist and can be read directly unless the user choose to overwrite the entire records.
- In the end, package the program into a single app for distribution

## Notes

1. This plan defines UI/UX contracts, data display, and behaviour requirements.
2. Detailed tax-rule calculation logic is out of scope for this plan.