from flask import Flask, render_template, request, jsonify
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Thread, Lock
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import yfinance as yf
import json, re, time, traceback
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
UNIVERSE_FILE = DATA_DIR / "nifty500_stocks.csv"
SCAN_FILE = DATA_DIR / "latest_scan.csv"
PORTFOLIO_FILE = DATA_DIR / "portfolio.json"
# Use a writable directory on Vercel; preserve local behavior.
if os.environ.get("VERCEL"):
    CACHE_DIR = Path("/tmp/history_cache")
else:
    CACHE_DIR = BASE_DIR / "data" / "history_cache"

CACHE_DIR.mkdir(parents=True, exist_ok=True)
app = Flask(__name__)
state_lock = Lock()
scan_state = {"running": False, "total": 0, "completed": 0, "message": "Ready", "started_at": None, "finished_at": None, "error": None}
results_cache = []
fundamental_cache = {}
history_cache = {}

def normalize_symbol(value):
    s = str(value).strip().upper()
    if not s or s in {"NAN", "NONE", "SYMBOL", "TICKER"}:
        return ""
    s = s.replace(" ", "")
    if s.endswith(".NS") or s.endswith(".BO"):
        return s
    return s + ".NS"

def load_symbols():
    if not UNIVERSE_FILE.exists():
        return []
    try:
        df = pd.read_csv(UNIVERSE_FILE)
    except Exception:
        return []
    if df.empty:
        return []
    candidates = ["Symbol", "symbol", "SYMBOL", "Ticker", "ticker", "Stock", "stock", "YahooTicker"]
    col = next((c for c in candidates if c in df.columns), df.columns[0])
    return list(dict.fromkeys([normalize_symbol(x) for x in df[col].dropna().tolist() if normalize_symbol(x)]))

def safe_float(x):
    try:
        if x is None or pd.isna(x):
            return None
        return float(x)
    except Exception:
        return None

def pct_change(a, b):
    try:
        if b is None or pd.isna(b) or float(b) == 0:
            return None
        return (float(a) / float(b) - 1) * 100
    except Exception:
        return None

def rsi(series, length=14):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/length, min_periods=length, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/length, min_periods=length, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(100)

def get_history(symbol, period="5y", force=False):
    key = symbol.replace(".", "_")
    cache_path = CACHE_DIR / f"{key}.csv"
    if not force and cache_path.exists():
        try:
            cached = pd.read_csv(cache_path, index_col=0, parse_dates=True)
            if len(cached) > 10 and (pd.Timestamp.now(tz=None) - pd.Timestamp(cached.index[-1]).tz_localize(None)).days <= 1:
                return cached
        except Exception:
            pass
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period=period, interval="1d", auto_adjust=True, actions=False, timeout=20)
    if hist is None or hist.empty:
        return pd.DataFrame()
    hist = hist.rename(columns=lambda x: str(x).title())
    hist.index = pd.to_datetime(hist.index).tz_localize(None)
    hist = hist[~hist.index.duplicated(keep="last")].sort_index()
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col not in hist:
            hist[col] = np.nan
    hist.to_csv(cache_path)
    history_cache[symbol] = hist
    return hist

def breakout_stats(hist):
    if hist.empty or len(hist) < 30:
        return {}
    close, high = hist["Close"], hist["High"]
    rolling_high = high.rolling(252, min_periods=1).max()
    current_close = float(close.iloc[-1])
    current_high = float(high.iloc[-1])
    high_52w = float(rolling_high.iloc[-1])
    below = max(0.0, (high_52w - current_close) / high_52w * 100) if high_52w else None
    # A breakout day is a close at/above the prior 252-session high, calculated without the current day.
    prior_high = high.shift(1).rolling(252, min_periods=30).max()
    breakout_mask = close >= prior_high
    breakout_dates = hist.index[breakout_mask.fillna(False)]
    previous_breakout = None
    if len(breakout_dates) >= 2:
        previous_breakout = breakout_dates[-2]
    elif len(breakout_dates) == 1 and breakout_dates[-1] != hist.index[-1]:
        previous_breakout = breakout_dates[-1]
    current_breakout = bool(pd.notna(prior_high.iloc[-1]) and current_close >= float(prior_high.iloc[-1]) * 0.999)
    if current_breakout and len(breakout_dates) >= 2:
        prior_candidates = [d for d in breakout_dates if d < hist.index[-1]]
        previous_breakout = prior_candidates[-1] if prior_candidates else None
    days_since = int((hist.index[-1] - previous_breakout).days) if previous_breakout is not None else None
    # Keep intraday touch separate from the closing-price classification.
    # A stock is labelled "At 52-week high" only when its CLOSE is within 0.1% of the 52-week high.
    intraday_high_touch = current_high >= high_52w * 0.999
    if current_breakout and days_since is not None and days_since >= 90:
        status = "Breakout after long gap"
    elif below is not None and below <= 0.1:
        status = "At 52-week high"
    elif below is not None and below <= 5:
        status = "Near 52-week high"
    else:
        status = "Below 52-week high"
    return {
        "price": safe_float(current_close),
        "high_52w": safe_float(high_52w),
        "below_52w_pct": safe_float(below),
        "status": status,
        # Legacy field retained for compatibility; it represents an intraday high touch.
        "is_52w_high": bool(intraday_high_touch),
        "intraday_high_touch_52w": bool(intraday_high_touch),
        "is_breakout": bool(current_breakout),
        "days_since_previous_breakout": days_since,
        "previous_breakout_date": previous_breakout.strftime("%Y-%m-%d") if previous_breakout is not None else None,
        "last_date": hist.index[-1].strftime("%Y-%m-%d"),
        "volume": safe_float(hist["Volume"].iloc[-1]),
    }

def get_fundamentals(symbol, force=False):
    if not force and symbol in fundamental_cache:
        return fundamental_cache[symbol]
    out = {"symbol": symbol}
    try:
        info = yf.Ticker(symbol).get_info()
        if not isinstance(info, dict):
            info = {}
        fields = {
            "company_name": "longName", "sector": "sector", "industry": "industry",
            "market_cap": "marketCap", "trailing_pe": "trailingPE", "forward_pe": "forwardPE",
            "price_to_book": "priceToBook", "roe": "returnOnEquity", "profit_margin": "profitMargins",
            "operating_margin": "operatingMargins", "revenue_growth": "revenueGrowth",
            "earnings_growth": "earningsGrowth", "debt_to_equity": "debtToEquity",
            "free_cashflow": "freeCashflow", "dividend_yield": "dividendYield",
            "beta": "beta", "website": "website", "summary": "longBusinessSummary",
        }
        for dest, src in fields.items():
            val = info.get(src)
            if isinstance(val, (int, float, np.number)):
                val = float(val)
            out[dest] = val
        out["market_cap_cr"] = (float(info["marketCap"]) / 1e7) if info.get("marketCap") else None
        for k in ["roe", "profit_margin", "operating_margin", "revenue_growth", "earnings_growth", "dividend_yield"]:
            if out.get(k) is not None:
                out[k] = out[k] * 100
    except Exception as exc:
        out["error"] = str(exc)[:180]
    fundamental_cache[symbol] = out
    return out

def scan_one(symbol):
    try:
        hist = get_history(symbol, period="5y")
        stats = breakout_stats(hist)
        if not stats:
            return {"symbol": symbol, "status": "No data", "error": "No usable price history"}
        # Pull fundamentals for current/new highs and near-high candidates only; keeps a 500-stock scan practical.
        if stats.get("is_52w_high") or stats.get("is_breakout") or (stats.get("below_52w_pct") is not None and stats["below_52w_pct"] <= 5):
            fund = get_fundamentals(symbol)
        else:
            fund = {}
        return {
            "symbol": symbol,
            "company_name": fund.get("company_name") or symbol.replace(".NS", ""),
            **stats,
            "sector": fund.get("sector"),
            "market_cap_cr": fund.get("market_cap_cr"),
            "trailing_pe": fund.get("trailing_pe"),
            "roe": fund.get("roe"),
            "revenue_growth": fund.get("revenue_growth"),
            "profit_margin": fund.get("profit_margin"),
        }
    except Exception as exc:
        return {"symbol": symbol, "status": "Error", "error": str(exc)[:200]}

def run_scan():
    global results_cache
    symbols = load_symbols()
    with state_lock:
        scan_state.update({"running": True, "total": len(symbols), "completed": 0, "message": "Scanning market data…", "started_at": datetime.now().isoformat(timespec="seconds"), "finished_at": None, "error": None})
    if not symbols:
        with state_lock:
            scan_state.update({"running": False, "message": "No symbols found in data/nifty500_stocks.csv.", "error": "Missing universe"})
        return
    out = []
    try:
        # Moderate concurrency reduces the chance of triggering provider throttling.
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(scan_one, s): s for s in symbols}
            for fut in as_completed(futures):
                try:
                    out.append(fut.result())
                except Exception as exc:
                    out.append({"symbol": futures[fut], "status": "Error", "error": str(exc)[:180]})
                with state_lock:
                    scan_state["completed"] += 1
                    scan_state["message"] = f"Processed {scan_state['completed']} of {scan_state['total']} symbols"
        df = pd.DataFrame(out)
        order = {"Breakout after long gap": 0, "At 52-week high": 1, "Near 52-week high": 2, "Below 52-week high": 3, "No data": 4, "Error": 5}
        if not df.empty:
            df["_sort"] = df["status"].map(order).fillna(9)
            df = df.sort_values(["_sort", "below_52w_pct"], ascending=[True, True]).drop(columns=["_sort"])
            df.to_csv(SCAN_FILE, index=False)
        with state_lock:
            # Convert pandas NaN/NaT values to JSON-safe None; NaN can break browser JSON parsing.
            clean_df = df.astype(object).where(pd.notna(df), None)
            results_cache = clean_df.to_dict(orient="records")
            scan_state.update({"running": False, "message": f"Scan complete · {len(out)} symbols", "finished_at": datetime.now().isoformat(timespec="seconds")})
    except Exception as exc:
        with state_lock:
            scan_state.update({"running": False, "message": "Scan failed", "error": str(exc)[:500]})

def ensure_results():
    global results_cache
    if results_cache:
        return
    if SCAN_FILE.exists():
        try:
            df = pd.read_csv(SCAN_FILE)
            # Use object dtype before replacing missing values so they become Python None.
            df = df.astype(object).where(pd.notna(df), None)
            results_cache = df.to_dict(orient="records")
        except Exception:
            pass

@app.route("/")
def index():
    ensure_results()
    return render_template("index.html", universe_count=len(load_symbols()))

@app.get("/api/status")
def api_status():
    with state_lock:
        return jsonify(dict(scan_state))

@app.post("/api/scan")
def api_scan():
    with state_lock:
        if scan_state["running"]:
            return jsonify({"ok": True, "message": "Scan already running"})
        if not load_symbols():
            return jsonify({"ok": False, "message": "No symbols found in data/nifty500_stocks.csv. Check the configured universe file."}), 400
        Thread(target=run_scan, daemon=True).start()
    return jsonify({"ok": True, "message": "Scan started"})

@app.get("/api/results")
def api_results():
    ensure_results()
    rows = results_cache
    status = request.args.get("status", "all")
    search = request.args.get("q", "").strip().lower()
    if status != "all":
        rows = [r for r in rows if r.get("status") == status]
    if search:
        rows = [r for r in rows if search in str(r.get("symbol", "")).lower() or search in str(r.get("company_name", "")).lower() or search in str(r.get("sector", "")).lower()]
    return jsonify({"rows": rows, "count": len(rows)})

@app.get("/api/stock/<path:symbol>")
def api_stock(symbol):
    symbol = normalize_symbol(symbol)
    force = request.args.get("refresh") == "1"
    period = request.args.get("period", "5y")
    allowed = {"1d", "1w", "1mo", "3mo", "6mo", "1y", "3y", "5y", "max"}
    if period not in allowed:
        period = "5y"
    try:
        # Daily history remains the source of 52-week stats and long-horizon returns.
        daily = get_history(symbol, period="max" if period == "max" else "5y", force=(force or period == "max"))
        if daily.empty:
            return jsonify({"ok": False, "message": f"No historical data found for {symbol}. Check the symbol format."}), 404
        fund = get_fundamentals(symbol, force=force)
        daily = daily.copy()
        daily["EMA20"] = daily["Close"].ewm(span=20, adjust=False).mean()
        daily["EMA50"] = daily["Close"].ewm(span=50, adjust=False).mean()
        daily["EMA200"] = daily["Close"].ewm(span=200, adjust=False).mean()
        daily["RSI"] = rsi(daily["Close"])
        periods = {"1W": 5, "1M": 21, "3M": 63, "6M": 126, "12M": 252, "3Y": 756, "5Y": 1260}
        daily_returns = {}
        for label, n in periods.items():
            daily_returns[label] = ((daily["Close"] / daily["Close"].shift(n)) - 1) * 100
        daily_returns["Since available"] = ((daily["Close"] / daily["Close"].iloc[0]) - 1) * 100
        daily["High52"] = daily["High"].rolling(252, min_periods=1).max()

        intraday = period in {"1d", "1w"}
        if intraday:
            interval = "5m" if period == "1d" else "15m"
            intraday_period = "1d" if period == "1d" else "5d"
            chart = yf.Ticker(symbol).history(period=intraday_period, interval=interval, auto_adjust=True, actions=False, timeout=20)
            if chart is None or chart.empty:
                # Fallback to recent daily bars if intraday history is unavailable.
                chart = daily.tail(1 if period == "1d" else 5).copy()
                intraday = False
            else:
                chart = chart.rename(columns=lambda x: str(x).title())
                chart.index = pd.to_datetime(chart.index)
                if chart.index.tz is not None:
                    chart.index = chart.index.tz_localize(None)
                chart = chart[~chart.index.duplicated(keep="last")].sort_index()
                for col in ["Open", "High", "Low", "Close", "Volume"]:
                    if col not in chart:
                        chart[col] = np.nan
                chart["EMA20"] = chart["Close"].ewm(span=20, adjust=False).mean()
                chart["EMA50"] = chart["Close"].ewm(span=50, adjust=False).mean()
                chart["EMA200"] = chart["Close"].ewm(span=200, adjust=False).mean()
                chart["RSI"] = rsi(chart["Close"])
                chart["High52"] = float(daily["High52"].iloc[-1])
        else:
            chart = daily.copy()
            start_date = {
                "1mo": pd.Timestamp.now() - pd.DateOffset(months=1),
                "3mo": pd.Timestamp.now() - pd.DateOffset(months=3),
                "6mo": pd.Timestamp.now() - pd.DateOffset(months=6),
                "1y": pd.Timestamp.now() - pd.DateOffset(years=1),
                "3y": pd.Timestamp.now() - pd.DateOffset(years=3),
                "5y": pd.Timestamp.now() - pd.DateOffset(years=5),
                "max": chart.index[0],
            }[period]
            chart = chart[chart.index >= pd.Timestamp(start_date).tz_localize(None)]
            if chart.empty:
                chart = daily.tail(250)

        rows = []
        daily_index = daily.index
        for idx, row in chart.iterrows():
            # Returns in hover tooltip are daily holding-period returns as of the nearest
            # available daily close for the bar's date; intraday chart prices remain intraday.
            point_date = pd.Timestamp(idx).tz_localize(None) if pd.Timestamp(idx).tzinfo else pd.Timestamp(idx)
            eligible = daily_index[daily_index <= point_date.normalize()]
            daily_pos = daily_index.get_loc(eligible[-1]) if len(eligible) else len(daily_index) - 1
            returns = {}
            for label, series in daily_returns.items():
                returns[label] = safe_float(series.iloc[daily_pos])
            date_label = idx.strftime("%Y-%m-%d %H:%M") if intraday else idx.strftime("%Y-%m-%d")
            rows.append({
                "date": date_label,
                "open": safe_float(row["Open"]), "high": safe_float(row["High"]),
                "low": safe_float(row["Low"]), "close": safe_float(row["Close"]),
                "volume": safe_float(row["Volume"]), "ema20": safe_float(row["EMA20"]),
                "ema50": safe_float(row["EMA50"]), "ema200": safe_float(row["EMA200"]),
                "rsi": safe_float(row["RSI"]), "high52": safe_float(row["High52"]),
                "returns": returns,
            })
        stats = breakout_stats(daily)
        return jsonify({
            "ok": True, "symbol": symbol, "company_name": fund.get("company_name") or symbol,
            "period": period, "intraday": intraday, "data": rows, "fundamentals": fund, "stats": stats,
            "latest": rows[-1] if rows else None,
        })
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)[:400]}), 500



def load_portfolio():
    if not PORTFOLIO_FILE.exists():
        return []
    try:
        data = json.loads(PORTFOLIO_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []

def save_portfolio(items):
    PORTFOLIO_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

@app.get("/api/portfolio")
def api_portfolio_list():
    return jsonify({"ok": True, "holdings": load_portfolio()})

@app.post("/api/portfolio")
def api_portfolio_add():
    payload = request.get_json(silent=True) or {}
    symbol = normalize_symbol(payload.get("symbol", ""))
    try:
        quantity = int(payload.get("quantity", 0))
        buy_price = float(payload.get("buy_price", 0))
        buy_date = str(payload.get("buy_date", "")).strip()
        notes = str(payload.get("notes", "")).strip()[:500]
        if not symbol or quantity <= 0 or buy_price <= 0 or not buy_date:
            raise ValueError("Enter a valid symbol, quantity, buy price and buy date.")
        datetime.strptime(buy_date, "%Y-%m-%d")
        items = load_portfolio()
        next_id = max([int(x.get("id", 0)) for x in items] + [0]) + 1
        item = {
            "id": next_id, "symbol": symbol, "quantity": quantity,
            "buy_price": round(buy_price, 4), "buy_date": buy_date,
            "notes": notes, "created_at": datetime.now().isoformat(timespec="seconds")
        }
        items.append(item)
        save_portfolio(items)
        return jsonify({"ok": True, "message": f"Added {quantity} shares of {symbol}.", "holding": item})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

@app.delete("/api/portfolio/<int:holding_id>")
def api_portfolio_delete(holding_id):
    items = load_portfolio()
    updated = [x for x in items if int(x.get("id", -1)) != holding_id]
    if len(updated) == len(items):
        return jsonify({"ok": False, "message": "Holding not found."}), 404
    save_portfolio(updated)
    return jsonify({"ok": True, "holdings": updated})

@app.get("/api/portfolio/valuation")
def api_portfolio_valuation():
    items = load_portfolio()
    out = []
    for item in items:
        symbol = item.get("symbol", "")
        current = None
        try:
            # Fetch a short quote window directly; do not overwrite the long-history cache.
            hist = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=True, actions=False, timeout=20)
            if hist is not None and not hist.empty:
                current = safe_float(hist["Close"].iloc[-1])
        except Exception:
            current = None
        qty = int(item.get("quantity", 0))
        buy = float(item.get("buy_price", 0))
        invested = qty * buy
        value = qty * current if current is not None else None
        pnl = value - invested if value is not None else None
        ret = (pnl / invested * 100) if pnl is not None and invested else None
        out.append({**item, "current_price": current, "invested": invested,
                    "current_value": value, "pnl": pnl, "return_pct": ret})
    total_invested = sum(x["invested"] for x in out)
    priced = [x for x in out if x["current_value"] is not None]
    total_value = sum(x["current_value"] for x in priced)
    total_pnl = total_value - sum(x["invested"] for x in priced)
    total_cost_priced = sum(x["invested"] for x in priced)
    total_return = (total_pnl / total_cost_priced * 100) if total_cost_priced else None
    return jsonify({"ok": True, "holdings": out, "total_invested": total_invested,
                    "current_value": total_value, "pnl": total_pnl, "return_pct": total_return,
                    "unpriced_count": len(out)-len(priced)})

@app.get("/api/buy-setup/<path:symbol>")
def api_buy_setup(symbol):
    symbol = normalize_symbol(symbol)
    try:
        hist = get_history(symbol, period="5y")
        if hist.empty or len(hist) < 30:
            return jsonify({"ok": False, "message": "Insufficient price history for this ticker."}), 404
        close = hist["Close"]
        last = float(close.iloc[-1])
        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
        ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])
        stats = breakout_stats(hist)
        fund = get_fundamentals(symbol)
        # Transparent rule-based checklist, not an individualized buy recommendation.
        tech_points = sum([last > ema20, last > ema50, last > ema200])
        rsi_value = safe_float(rsi(close).iloc[-1])
        tech_notes = []
        tech_notes.append("Price is above EMA 20" if last > ema20 else "Price is below EMA 20")
        tech_notes.append("Price is above EMA 50" if last > ema50 else "Price is below EMA 50")
        tech_notes.append("Price is above EMA 200" if last > ema200 else "Price is below EMA 200")
        if rsi_value is not None:
            tech_notes.append("RSI is elevated; avoid chasing if above 70" if rsi_value > 70 else ("RSI is weak below 40" if rsi_value < 40 else "RSI is between 40 and 70"))
        fundamental_checks = [
            ("ROE", fund.get("roe"), 0.15),
            ("Revenue growth", fund.get("revenue_growth"), 0.10),
            ("Profit margin", fund.get("profit_margin"), 0.05),
        ]
        fund_good = 0
        fund_known = 0
        fund_notes = []
        for label, value, threshold in fundamental_checks:
            if value is not None:
                fund_known += 1
                ok = float(value) >= threshold
                fund_good += int(ok)
                fund_notes.append(f"{label}: {'passes' if ok else 'review'} ({float(value)*100:.1f}%)")
            else:
                fund_notes.append(f"{label}: unavailable; verify latest filings")
        de = fund.get("debt_to_equity")
        if de is not None:
            fund_known += 1
            ok = float(de) <= 1.0
            fund_good += int(ok)
            fund_notes.append(f"Debt/equity: {'within heuristic threshold' if ok else 'high; review'} ({float(de):.2f})")
        else:
            fund_notes.append("Debt/equity: unavailable; verify latest filings")
        if fund_known == 0:
            fund_label = "Insufficient fundamental data"
        elif fund_good == fund_known:
            fund_label = "Checklist looks constructive"
        elif fund_good == 0:
            fund_label = "Fundamentals need careful review"
        else:
            fund_label = "Mixed fundamental picture"
        # Potential observation zones based on moving averages; not guaranteed support.
        zone_low = min(ema20, ema50)
        zone_high = max(ema20, ema50)
        if last > max(ema20, ema50):
            entry_note = f"Watch for a pullback toward EMA 20–50 (approx. ₹{zone_low:.2f}–₹{zone_high:.2f}); wait for price confirmation."
        elif last > ema200:
            entry_note = f"Trend is mixed; observe whether price reclaims EMA 20/50 (approx. ₹{zone_low:.2f}–₹{zone_high:.2f}) before considering entry."
        else:
            entry_note = f"Price is below EMA 200; consider waiting for trend improvement rather than assuming this is a bargain. EMA 20–50 zone: ₹{zone_low:.2f}–₹{zone_high:.2f}."
        return jsonify({
            "ok": True, "symbol": symbol, "company_name": fund.get("company_name") or symbol,
            "price": last, "ema20": ema20, "ema50": ema50, "ema200": ema200,
            "rsi": rsi_value, "technical_score": tech_points, "technical_notes": tech_notes,
            "fundamental_label": fund_label, "fundamental_notes": fund_notes,
            "entry_note": entry_note, "watch_zone_low": zone_low, "watch_zone_high": zone_high,
            "status": stats.get("status"), "below_52w_pct": stats.get("below_52w_pct"),
            "warning": "Rule-based research checklist only, not personalized financial advice. Moving averages are not guaranteed support; verify current filings and live prices."
        })
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)[:400]}), 500


@app.get("/api/export")
def api_export():
    ensure_results()
    if not SCAN_FILE.exists():
        return jsonify({"ok": False, "message": "No scan results yet"}), 404
    from flask import send_file
    return send_file(SCAN_FILE, as_attachment=True, download_name="nifty500_52week_scan.csv")


@app.get("/api/download/history.txt")
def api_download_history():
    from flask import send_file
    path = APP_DIR / "output" / "nifty500_52week_history.txt"
    if not path.exists():
        return jsonify({"ok": False, "message": "No daily history yet. Run daily_scan.py first."}), 404
    return send_file(path, as_attachment=True, download_name="nifty500_52week_history.txt", mimetype="text/plain")

@app.get("/api/download/rolling.csv")
def api_download_rolling():
    from flask import send_file
    path = APP_DIR / "output" / "nifty500_rolling_30days.csv"
    if not path.exists():
        return jsonify({"ok": False, "message": "No rolling scan history yet. Run daily_scan.py first."}), 404
    return send_file(path, as_attachment=True, download_name="nifty500_rolling_30days.csv", mimetype="text/csv")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
