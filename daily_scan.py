"""Run a full Nifty 500 scan and maintain compact daily output files.

Outputs:
- data/latest_scan.csv: current full-universe scan (existing dashboard file)
- output/nifty500_52week_history.txt: append-only daily snapshots of stocks within 5% of 52-week high
- output/nifty500_rolling_30days.csv: rolling 30-calendar-day qualifying stock records
- output/latest_scan.csv: copy of the latest full scan for backup/download
"""
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import shutil
import sys
import pandas as pd

from app import APP_DIR, DATA_DIR, SCAN_FILE, load_symbols, run_scan, scan_state

OUTPUT_DIR = APP_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_TXT = OUTPUT_DIR / "nifty500_52week_history.txt"
ROLLING_CSV = OUTPUT_DIR / "nifty500_rolling_30days.csv"
LATEST_COPY = OUTPUT_DIR / "latest_scan.csv"
TZ = ZoneInfo("Asia/Kolkata")


def fmt(value, digits=2):
    try:
        if pd.isna(value):
            return "-"
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def apply_close_based_status(df):
    """Classify status from close-to-52-week-high distance, not intraday high alone."""
    out = df.copy()
    if "below_52w_pct" not in out.columns:
        out["below_52w_pct"] = pd.NA
    gap = pd.to_numeric(out["below_52w_pct"], errors="coerce")
    intraday_touch = out.get("is_52w_high", pd.Series(False, index=out.index))
    # Preserve the old boolean as a legacy field, while exposing its actual meaning clearly.
    out["intraday_high_touch_52w"] = intraday_touch.fillna(False).astype(bool)
    out["status"] = "Below 52-week high"
    out.loc[gap.notna() & (gap <= 5), "status"] = "Near 52-week high"
    out.loc[gap.notna() & (gap <= 0.1), "status"] = "At 52-week high"

    breakout = out.get("is_breakout", pd.Series(False, index=out.index)).fillna(False).astype(bool)
    days = pd.to_numeric(out.get("days_since_previous_breakout", pd.Series(pd.NA, index=out.index)), errors="coerce")
    long_gap = breakout & days.notna() & (days >= 90)
    out.loc[long_gap, "status"] = "Breakout after long gap"
    return out


def build_daily_block(df, scan_dt):
    stamp = scan_dt.strftime("%Y-%m-%d %H:%M:%S IST")
    eligible = df.copy()
    if "below_52w_pct" not in eligible:
        eligible["below_52w_pct"] = pd.NA
    gap = pd.to_numeric(eligible["below_52w_pct"], errors="coerce")
    eligible = eligible[(gap >= 0) & (gap <= 5)].copy()
    eligible["_gap"] = pd.to_numeric(eligible["below_52w_pct"], errors="coerce")
    eligible = eligible.sort_values("_gap", ascending=True)
    lines = [
        "=" * 78,
        f"SCAN DATE: {scan_dt:%Y-%m-%d}",
        f"SCAN TIME: {stamp}",
        "UNIVERSE: NIFTY 500 (or symbols in data/nifty500_stocks.csv)",
        "QUALIFICATION: at 52-week high or closing price within 5% of 52-week high",
        "",
        f"{'SYMBOL':<22} {'CLOSE':>12} {'52W HIGH':>12} {'GAP %':>9}  STATUS",
        "-" * 78,
    ]
    for _, row in eligible.iterrows():
        symbol = str(row.get("symbol", ""))
        close = fmt(row.get("price"))
        high = fmt(row.get("high_52w"))
        gapv = fmt(row.get("below_52w_pct"))
        status = str(row.get("status", "Near 52-week high"))
        if gapv != "-" and float(gapv) <= 0.1:
            status = "AT/WITHIN 0.1% OF HIGH"
        elif gapv != "-" and float(gapv) <= 2:
            status = "WITHIN 2%"
        else:
            status = "WITHIN 5%"
        lines.append(f"{symbol:<22} {close:>12} {high:>12} {gapv:>9}  {status}")
    lines += ["", f"SCANNED: {len(df)}", f"QUALIFIED: {len(eligible)}", ""]
    return "\n".join(lines), eligible


def update_history_txt(block, scan_date):
    # Idempotent per date: replace today's block if the workflow is rerun.
    existing = HISTORY_TXT.read_text(encoding="utf-8") if HISTORY_TXT.exists() else ""
    marker = f"SCAN DATE: {scan_date}"
    chunks = existing.split("=" * 78 + "\n")
    kept = []
    for chunk in chunks:
        if chunk.strip() and marker not in chunk:
            kept.append(chunk.rstrip())
    new_content = ("\n" + "=" * 78 + "\n").join(kept).strip()
    if new_content:
        new_content += "\n"
    HISTORY_TXT.write_text(new_content + block + "\n", encoding="utf-8")


def main():
    if not load_symbols():
        print("ERROR: No symbols found in data/nifty500_stocks.csv. Add your real Nifty 500 universe first.", file=sys.stderr)
        return 2
    scan_dt = datetime.now(TZ).replace(microsecond=0)
    print(f"Starting universe scan at {scan_dt.isoformat()}")
    run_scan()
    if scan_state.get("error") or not SCAN_FILE.exists():
        print(f"ERROR: scan failed: {scan_state.get('error') or scan_state.get('message')}", file=sys.stderr)
        return 1
    df = pd.read_csv(SCAN_FILE)
    if df.empty:
        print("ERROR: scan output is empty; existing history was not changed.", file=sys.stderr)
        return 1
    # Recalculate labels consistently even if the source scan was produced by an older app.py.
    df = apply_close_based_status(df)
    df.to_csv(SCAN_FILE, index=False)
    # Keep the latest full scan available for dashboard and backup.
    shutil.copy2(SCAN_FILE, LATEST_COPY)
    block, eligible = build_daily_block(df, scan_dt)
    update_history_txt(block, scan_dt.strftime("%Y-%m-%d"))

    eligible = eligible.copy()
    eligible.insert(0, "scan_timestamp_ist", scan_dt.strftime("%Y-%m-%d %H:%M:%S IST"))
    eligible.insert(1, "scan_date", scan_dt.strftime("%Y-%m-%d"))
    if ROLLING_CSV.exists():
        try:
            old = pd.read_csv(ROLLING_CSV)
        except Exception:
            old = pd.DataFrame()
    else:
        old = pd.DataFrame()
    combined = pd.concat([old, eligible], ignore_index=True, sort=False)
    if "scan_date" in combined.columns:
        # Replace any prior run for the same day; preserve earlier daily snapshots.
        combined = combined.drop_duplicates(subset=["scan_date", "symbol"], keep="last")
        cutoff = (scan_dt - timedelta(days=29)).date().isoformat()
        combined = combined[combined["scan_date"].astype(str) >= cutoff]
        combined = combined.sort_values(["scan_date", "below_52w_pct"], ascending=[False, True], na_position="last")
    combined.to_csv(ROLLING_CSV, index=False)
    print(f"Scan complete: {len(df)} symbols; {len(eligible)} within 5% of their 52-week highs.")
    print(f"TXT history: {HISTORY_TXT}")
    print(f"Rolling CSV: {ROLLING_CSV}")
    print(f"Latest full scan: {LATEST_COPY}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
