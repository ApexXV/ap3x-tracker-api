#!/usr/bin/env python3
"""
VOO & NVDA Portfolio Tracker v4
=================================
Runs continuously, logging a snapshot every 6 hours by default.

Fetch failure behavior:
  --once mode   : prompts for manual price entry if a fetch fails
  watch mode    : falls back to last known price; skips cycle if no
                  price has ever been fetched for that ticker

Usage:
    python voo_nvda_tracker.py <voo_shares> <nvda_shares>                  # watch mode (default)
    python voo_nvda_tracker.py <voo_shares> <nvda_shares> --once           # single snapshot then exit
    python voo_nvda_tracker.py <voo_shares> <nvda_shares> --install-deps   # install yfinance then run
"""

from __future__ import annotations

import csv
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── Config ───────────────────────────────────────────────────────────────────
TICKERS       = ["VOO", "NVDA"]
REFRESH_HOURS = 6
LOG_FILE      = Path(__file__).parent / "portfolio_log.csv"

# Fixed CSV schema — append-only after first write
CSV_FIELDS = [
    "timestamp", "total_value",
    "VOO_shares", "VOO_price", "VOO_value",
    "NVDA_shares", "NVDA_price", "NVDA_value",
]


# ── Dependency check ──────────────────────────────────────────────────────────

def check_yfinance(auto_install: bool = False) -> None:
    try:
        import yfinance  # noqa: F401
    except ImportError:
        if auto_install:
            import subprocess
            print("[setup] yfinance not found — installing (--install-deps was set)...")
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "--quiet", "yfinance"]
                )
                print("[setup] yfinance installed successfully.\n")
            except subprocess.CalledProcessError as exc:
                sys.exit(
                    f"\n  [error] yfinance installation failed (exit code {exc.returncode}).\n"
                    "  Try manually:  pip install yfinance\n"
                )
        else:
            sys.exit(
                "\n  [error] yfinance is not installed.\n"
                "  Activate your venv and run:  pip install yfinance\n"
                "  Or pass --install-deps to have this script install it for you.\n"
            )


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class Position:
    ticker: str
    shares: float
    price: float = 0.0

    @property
    def value(self) -> float:
        return self.shares * self.price


# ── Price fetching ────────────────────────────────────────────────────────────

def fetch_price(ticker: str) -> float | None:
    """Attempt to fetch a live price. Returns None on any failure."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)

        # 1) fast_info
        try:
            fi = t.fast_info
            for key in ("lastPrice", "last_price", "regularMarketPrice"):
                val = getattr(fi, key, None)
                if val is not None:
                    return float(val)
        except Exception:
            pass

        # 2) intraday history
        try:
            hist = t.history(period="1d", interval="1m")
            if not hist.empty:
                return float(hist["Close"].dropna().iloc[-1])
        except Exception:
            pass

        # 3) daily close fallback
        try:
            hist = t.history(period="5d")
            if not hist.empty:
                return float(hist["Close"].dropna().iloc[-1])
        except Exception:
            pass

    except Exception as exc:
        print(f"  [warn] yfinance error for {ticker}: {exc}")

    return None


def get_prices(tickers: list[str], last_known: dict[str, float], interactive: bool) -> dict[str, float] | None:
    """
    Fetch prices for all tickers.

    interactive=True  (--once mode):
        Failed fetches prompt for manual entry.

    interactive=False (watch mode):
        Failed fetches fall back to last known price.
        If no last known price exists, the cycle is skipped (returns None).
    """
    prices: dict[str, float] = {}
    failed: list[str] = []

    print("  Fetching prices...")
    for ticker in tickers:
        price = fetch_price(ticker)
        if price is not None:
            prices[ticker] = price
            print(f"    {ticker:6s}  ${price:>10.2f}  ✓")
        else:
            failed.append(ticker)

    if failed:
        if interactive:
            print()
            for ticker in failed:
                while True:
                    raw = input(f"  Enter current price for {ticker}: $").strip()
                    try:
                        prices[ticker] = float(raw)
                        break
                    except ValueError:
                        print("  Please enter a valid number.")
        else:
            for ticker in failed:
                if ticker in last_known:
                    prices[ticker] = last_known[ticker]
                    print(f"    {ticker:6s}  fetch failed — using last known price ${last_known[ticker]:.2f}")
                else:
                    print(f"    {ticker:6s}  fetch failed — no last known price available, skipping cycle")
                    return None

    return prices


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_report(positions: list[Position], timestamp: datetime) -> float:
    total  = sum(p.value for p in positions)
    border = "─" * 62

    print(f"\n  ┌{border}┐")
    print(f"  │{'Portfolio Snapshot':^62}│")
    print(f"  │  {timestamp.strftime('%A, %B %d %Y  •  %I:%M %p'):^58}  │")
    print(f"  ├{border}┤")
    print(f"  │  {'Ticker':<8} {'Shares':>10} {'Price':>12} {'Value':>12} {'Alloc':>9}  │")
    print(f"  ├{border}┤")

    for p in positions:
        alloc = (p.value / total * 100) if total > 0 else 0.0
        print(
            f"  │  {p.ticker:<8}"
            f" {p.shares:>10.6f}"
            f" ${p.price:>11.2f}"
            f" ${p.value:>11.2f}"
            f" {alloc:>8.2f}%  │"
        )

    print(f"  ├{border}┤")
    print(f"  │  {'TOTAL':<8} {'':>10} {'':>12} ${total:>11.2f} {'100.00%':>9}  │")
    print(f"  └{border}┘\n")
    return total


# ── CSV logging — append-only ─────────────────────────────────────────────────

def save_snapshot(positions: list[Position], total: float, ts: datetime) -> None:
    pos = {p.ticker: p for p in positions}

    row = {
        "timestamp":   ts.isoformat(timespec="seconds"),
        "total_value": round(total, 2),
        "VOO_shares":  round(pos["VOO"].shares, 8),
        "VOO_price":   round(pos["VOO"].price,  2),
        "VOO_value":   round(pos["VOO"].value,  2),
        "NVDA_shares": round(pos["NVDA"].shares, 8),
        "NVDA_price":  round(pos["NVDA"].price,  2),
        "NVDA_value":  round(pos["NVDA"].value,  2),
    }

    write_header = not LOG_FILE.exists()

    with LOG_FILE.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    print(f"  Snapshot saved → {LOG_FILE}")


# ── Single cycle ──────────────────────────────────────────────────────────────

def run_cycle(
    voo_shares: float,
    nvda_shares: float,
    last_known: dict[str, float],
    interactive: bool,
) -> None:
    positions = [
        Position(ticker="VOO",  shares=voo_shares),
        Position(ticker="NVDA", shares=nvda_shares),
    ]

    prices = get_prices(TICKERS, last_known, interactive)
    if prices is None:
        print("  [warn] Cycle skipped — fetch failed with no last known price to fall back on.")
        return

    for p in positions:
        p.price = prices[p.ticker]

    # Update last known prices for next cycle
    for p in positions:
        last_known[p.ticker] = p.price

    ts    = datetime.now()
    total = print_report(positions, ts)
    save_snapshot(positions, total, ts)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]

    once         = "--once"         in args
    install_deps = "--install-deps" in args
    args         = [a for a in args if not a.startswith("--")]

    if len(args) < 2:
        print(
            "\n  Usage:\n"
            "    python voo_nvda_tracker.py <voo_shares> <nvda_shares> [--once] [--install-deps]\n"
        )
        sys.exit(1)

    try:
        voo_shares  = float(args[0])
        nvda_shares = float(args[1])
    except ValueError:
        sys.exit("\n  [error] Share quantities must be numbers (e.g. 0.075, 3.5).\n")

    if voo_shares < 0 or nvda_shares < 0:
        sys.exit("\n  [error] Share quantities cannot be negative.\n")

    check_yfinance(auto_install=install_deps)

    print("=" * 66)
    print(" VOO & NVDA Portfolio Tracker v4")
    print(f" VOO: {voo_shares} shares   |   NVDA: {nvda_shares} shares")
    if once:
        print(" Mode: single snapshot")
    else:
        print(f" Mode: watch — refreshing every {REFRESH_HOURS} hours  •  Ctrl+C to stop")
        print(" Fetch failures: last known price fallback, skip cycle if none available")
    print("=" * 66)

    # Shared state across cycles — tracks last successfully fetched price per ticker
    last_known: dict[str, float] = {}

    if once:
        run_cycle(voo_shares, nvda_shares, last_known, interactive=True)
        return

    cycle = 1
    while True:
        print(f"\n[cycle {cycle}]")
        try:
            run_cycle(voo_shares, nvda_shares, last_known, interactive=False)
        except Exception as exc:
            print(f"  [error] Cycle failed unexpectedly: {exc}")

        print(f"\n  Next refresh in {REFRESH_HOURS} hours — press Ctrl+C to exit.\n")
        try:
            time.sleep(REFRESH_HOURS * 3600)
        except KeyboardInterrupt:
            print("\n  Stopped. Goodbye.")
            break

        cycle += 1


if __name__ == "__main__":
    main()
