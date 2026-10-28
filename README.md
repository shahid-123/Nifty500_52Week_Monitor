# 52W Lens — Nifty 500 Stock Intelligence

A local Flask website for monitoring:
- Stocks at or close to their rolling 52-week highs.
- Fresh breakouts and breakouts after a long gap.
- Distance from the 52-week high in percentage terms.
- Technical chart with price, EMA 20/50/200 and RSI (14).
- Hover returns for 1W, 1M, 3M, 6M, 12M, 3Y, 5Y and since available history.
- Fundamental snapshot including valuation, margins, growth, leverage and cash flow.
- CSV export of the latest scan.

## 1. Install (Windows)

Open Command Prompt in this folder:

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If `py -3.12` is unavailable, use the Python version installed on your machine.

## 2. Add your Nifty 500 list

1. Run the app once (step 3).
2. Open `http://127.0.0.1:5000`.
3. In **Universe setup**, upload your CSV.
4. The CSV should have a `Symbol` column or stock symbols in the first column. NSE symbols like `RELIANCE`, `HDFCBANK`, `TCS` are accepted; `.NS` is added automatically. Existing `.NS` or `.BO` symbols are preserved.

A sample file is included at `data/nifty500_stocks.csv`. Replace it with your own list through the upload control.

## 3. Run the website

```bat
.venv\Scripts\activate
python app.py
```

Open `http://127.0.0.1:5000` in your browser. Keep the terminal open while using the website.

## 4. Daily workflow

1. Upload/update your Nifty 500 symbol list if needed.
2. Click **Run universe scan**.
3. The scan runs in the background; progress is shown in the overview.
4. Use **Highs & breakouts** to filter/search and export the scan CSV.
5. Click a stock in the table or open **Stock intelligence** and enter a symbol.
6. Select a timeframe (1M to MAX); hover the chart to view historical returns.

## Definitions and caveats

- The 52-week high is calculated from the highest daily **adjusted** price high in the most recent 252 trading sessions available from the provider. For practical screening, adjusted history can differ slightly from the exchange's official unadjusted 52-week high.
- “At 52-week high” means the current day's high is within 0.1% of the rolling 252-session high.
- “Near 52-week high” means the closing price is within 5% of the rolling high.
- “Breakout after long gap” is a heuristic: the latest close is at/above the prior 252-session high and the previous qualifying breakout close was at least 90 calendar days earlier. It is not a guarantee of future performance.
- “Since available” means since the earliest price point returned by Yahoo Finance for that ticker, not necessarily the company's true inception/listing date.
- Fundamental values may be missing, delayed, stale, or defined differently by the data provider. Validate with company filings and exchange disclosures.
- A 500-stock scan can take several minutes. Yahoo Finance may throttle or omit some symbols. Retry failed tickers later.
- Yahoo Finance data access through `yfinance` is an unofficial interface and is intended for personal/research use. Review the provider's terms. This app is not affiliated with Yahoo Finance.
- This is a research tool, not investment advice. A 52-week high or technical breakout alone is not a buy signal.

## Files and folders

- `app.py` — Flask server, scan logic, technical calculations and API endpoints.
- `templates/index.html` — website layout.
- `static/style.css` — responsive styling.
- `static/app.js` — filters, charts, hover returns and UI actions.
- `data/nifty500_stocks.csv` — sample universe file; upload your own list in the app.
- `data/history_cache/` — cached historical price data.
- `data/latest_scan.csv` — most recent scan results.

## Troubleshooting

- **No data:** Check the symbol on Yahoo Finance. For NSE, the common format is `SYMBOL.NS`.
- **Scan seems slow:** Wait for the progress count to move; 500 tickers require many provider requests. Avoid starting multiple scans at once.
- **Some fundamentals are blank:** This can happen when Yahoo Finance does not expose a field for a particular Indian ticker.
- **Chart does not render:** Internet access is required for the Plotly CDN script. The backend also needs internet access to retrieve market data.
- **Port 5000 in use:** Close the other application using port 5000 or change the port at the bottom of `app.py`.


## Investment tracker (new)
- Open **Investment tracker** from the left navigation.
- Record symbol, quantity, buy price and buy date; this logs an investment only and does not place broker orders.
- Portfolio entries are stored locally in `data/portfolio.json`.
- Refresh prices to calculate approximate current value, unrealised P/L and return.
- Buy setup analysis is a transparent rule-based checklist using EMA 20/50/200, RSI and available Yahoo Finance fundamentals. It is not personalized investment advice; verify company filings and current market data.

## Deployment note (important)
This version is designed for local use. Its portfolio records and scan/cache files use local disk, and the scanner starts background work in the Flask process. For a reliable Vercel deployment, migrate portfolio persistence to managed PostgreSQL (for example Supabase/Neon), configure secrets as Vercel environment variables, and adapt long-running universe scans to a scheduled/background job. Do not assume local JSON files persist across serverless deployments.


## Daily text history and rolling output

Run `python daily_scan.py` after activating the virtual environment. It scans the symbols in `data/nifty500_stocks.csv`, writes the full latest scan to `data/latest_scan.csv` and `output/latest_scan.csv`, appends a dated snapshot of stocks at/within 5% of the 52-week high to `output/nifty500_52week_history.txt`, and maintains `output/nifty500_rolling_30days.csv` for the latest 30 calendar days. Re-running on the same date replaces that day's entries rather than duplicating them.

Download endpoints: `/api/download/history.txt` and `/api/download/rolling.csv`. The existing latest full scan export remains available from the dashboard and `/api/export`.

## GitHub + Vercel deployment (read-only dashboard with automated scans)

**Important:** Vercel serverless local disk is not durable. This deployment uses GitHub Actions to run the scan on weekdays at 16:45 IST, commit the updated CSV/TXT outputs to the repository, and let Vercel redeploy the updated dashboard. The first deployment requires you to upload your complete real Nifty 500 symbol CSV; the included CSV may be only a starter/sample file. Verify that `data/nifty500_stocks.csv` contains the full universe before pushing.

1. Test locally first: install requirements, run `python app.py`, open `http://127.0.0.1:5000`, and trigger a scan. Stop the server and run `python daily_scan.py`. Verify the three output files.
2. Create a GitHub repository. In the project directory run `git init`, `git add .`, `git commit -m "Initial Nifty500 monitor"`, `git branch -M main`, `git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git`, and `git push -u origin main`. If Git is already initialized, do not repeat `git init` or add a second remote.
3. In GitHub, open **Settings → Actions → General → Workflow permissions** and ensure workflows can write repository contents (the workflow also declares `contents: write`). In **Actions**, manually run **Daily Nifty 500 52-week scan** once. Check the logs and verify updated `output/` files were committed.
4. In Vercel, choose **Add New → Project**, import the GitHub repository, keep the project root at the repository root, and deploy. The `vercel.json` rewrite routes requests to the Flask app. Every successful daily output commit triggers a new deployment.
5. Download backups from the dashboard endpoints or directly from the repository. Keep your own local backup of TXT/CSV/XLSX as desired.

### Deployment limitations

- Yahoo Finance may throttle scheduled scans; check the GitHub Actions run log and rerun manually if needed. The cron runs on weekdays, not exchange holiday calendars.
- The GitHub Action updates the tracked scan outputs; it does not upload your private local `data/history_cache/` or portfolio JSON. Do not commit personal portfolio data to a public repository.
- The investment tracker in this starter version stores records in local JSON. That is suitable for local testing only, **not durable on Vercel**. Before using investment tracking on a deployed site, connect managed PostgreSQL (e.g. Neon/Supabase), add authentication and access controls, and migrate portfolio API storage. The stock scan dashboard and its tracked output files can be deployed separately from that database work.
- Do not store API keys, passwords, or private financial data in the repository.
