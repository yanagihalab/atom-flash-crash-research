#!/usr/bin/env python3
"""Build publication figures for the IEICE technical-report manuscript."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FIGURES = ROOT / "figures"
FIGURES.mkdir(parents=True, exist_ok=True)

EVENT_LOW = pd.Timestamp("2025-10-10T21:20:37.689043Z")
EVENT_START = pd.Timestamp("2025-10-10T20:30:00Z")
EVENT_END = pd.Timestamp("2025-10-10T22:30:00Z")

BLUE = "#1769aa"
ORANGE = "#d95f02"
GREEN = "#2e7d32"
PURPLE = "#6a3d9a"
RED = "#b2182b"
GRAY = "#606060"


def configure() -> None:
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
            "font.size": 8.5,
            "axes.labelsize": 8.5,
            "axes.titlesize": 9.0,
            "legend.fontsize": 7.6,
            "xtick.labelsize": 7.5,
            "ytick.labelsize": 7.5,
            "axes.linewidth": 0.7,
            "lines.linewidth": 1.25,
            "savefig.transparent": False,
            "pdf.fonttype": 42,
        }
    )


def read_binance_kline(symbol: str) -> pd.DataFrame:
    archive = (
        ROOT
        / "data/raw/binance/spot/daily/klines"
        / symbol
        / "1m"
        / f"{symbol}-1m-2025-10-10.zip"
    )
    names = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "trade_count",
        "taker_buy_volume",
        "taker_buy_quote_volume",
        "ignore",
    ]
    with zipfile.ZipFile(archive) as zf:
        member = next(name for name in zf.namelist() if name.endswith(".csv"))
        frame = pd.read_csv(zf.open(member), header=None, names=names)
    frame["time"] = pd.to_datetime(frame["open_time"], unit="us", utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame[(frame["time"] >= EVENT_START) & (frame["time"] <= EVENT_END)].copy()


def read_coinbase() -> pd.DataFrame:
    path = ROOT / "data/raw/coinbase/spot/candles/ATOM-USD/60s/ATOM-USD-60s-2025-10-10.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    frame = pd.DataFrame(rows, columns=["epoch", "low", "high", "open", "close", "volume"])
    frame["time"] = pd.to_datetime(frame["epoch"], unit="s", utc=True)
    return frame[(frame["time"] >= EVENT_START) & (frame["time"] <= EVENT_END)].copy()


def save_event_prices() -> None:
    usdt = read_binance_kline("ATOMUSDT").set_index("time")
    usdc = read_binance_kline("ATOMUSDC").set_index("time")
    coinbase = read_coinbase().set_index("time")

    common = usdt[["low"]].join(usdc[["low"]], lsuffix="_usdt", rsuffix="_usdc", how="inner")
    common["discount"] = 100.0 * (common["low_usdc"] - common["low_usdt"]) / common["low_usdc"]

    fig, (ax1, ax2) = plt.subplots(
        2,
        1,
        figsize=(7.05, 4.35),
        sharex=True,
        gridspec_kw={"height_ratios": [2.2, 1]},
        constrained_layout=True,
    )
    ax1.plot(usdt.index, usdt["low"], color=RED, label="Binance ATOM/USDT")
    ax1.plot(usdc.index, usdc["low"], color=BLUE, label="Binance ATOM/USDC")
    ax1.plot(coinbase.index, coinbase["low"], color=GREEN, label="Coinbase ATOM/USD")
    ax1.axvline(EVENT_LOW, color="black", linewidth=0.8, linestyle="--")
    ax1.set_yscale("log")
    ax1.set_ylim(8e-4, 5.0)
    ax1.set_ylabel("One-minute low price")
    ax1.grid(True, which="both", linewidth=0.35, alpha=0.35)
    ax1.legend(loc="lower left", frameon=True, ncol=1)
    ax1.text(
        EVENT_LOW,
        0.00135,
        " 0.001 USDT",
        color=RED,
        ha="left",
        va="bottom",
        fontsize=7.5,
    )

    ax2.plot(common.index, common["discount"], color=PURPLE)
    ax2.axvline(EVENT_LOW, color="black", linewidth=0.8, linestyle="--")
    ax2.set_ylabel("USDT discount\nrelative to USDC (%)")
    ax2.set_xlabel("UTC on 10 October 2025")
    ax2.set_ylim(-2, 103)
    ax2.grid(True, linewidth=0.35, alpha=0.35)
    ax2.xaxis.set_major_locator(mdates.MinuteLocator(interval=20))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=mdates.UTC))
    fig.savefig(FIGURES / "event_market_prices.pdf", bbox_inches="tight")
    plt.close(fig)


def save_sell_sweep() -> None:
    frame = pd.read_csv(ROOT / "results/flash_sell_sequence.csv")
    frame = frame.sort_values("aggregate_trade_id").reset_index(drop=True)
    frame["cum_qty"] = frame["quantity_atom"].cumsum()
    sizes = 10 + 55 * np.sqrt(frame["quantity_atom"] / frame["quantity_atom"].max())

    fig, ax = plt.subplots(figsize=(7.05, 3.25), constrained_layout=True)
    ax.step(frame["cum_qty"], frame["price"], where="post", color=RED, linewidth=1.2)
    ax.scatter(
        frame["cum_qty"],
        frame["price"],
        s=sizes,
        color=RED,
        alpha=0.60,
        edgecolor="white",
        linewidth=0.25,
        zorder=3,
    )
    ax.set_yscale("log")
    ax.set_ylim(8e-4, 2.1)
    ax.set_xlim(0, frame["cum_qty"].max() * 1.02)
    ax.set_xlabel("Cumulative aggressive-sell quantity (ATOM)")
    ax.set_ylabel("Execution price (USDT)")
    ax.grid(True, which="both", linewidth=0.35, alpha=0.35)
    ax.text(
        0.02,
        0.05,
        "92 contiguous raw trades; 71 aggregate rows; 9,695.33 ATOM\n"
        "All executions at 21:20:37.689043 UTC; buyer-is-maker = true",
        transform=ax.transAxes,
        fontsize=7.6,
        va="bottom",
        bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "alpha": 0.86, "edgecolor": "0.75"},
    )
    fig.savefig(FIGURES / "same_microsecond_sell_sweep.pdf", bbox_inches="tight")
    plt.close(fig)


def save_event_percentiles() -> None:
    tests = pd.read_csv(ROOT / "results/publication_window_tests.csv")
    tests = tests[tests["control_set"] == "all_360"].set_index("metric")
    selected = [
        ("binance_usdt_range_pct_of_open", "USDT price range", False),
        ("binance_usdt_trade_count", "USDT trade count", False),
        ("max_usdt_vs_usdc_low_discount_pct", "USDT-USDC low discount", False),
        ("confirmed_exchange_in_atom", "Confirmed exchange inflow", False),
        ("confirmed_exchange_out_atom", "Confirmed exchange outflow", False),
        ("all_behavioral_candidate_in_atom", "Confirmed + unconfirmed inflow", True),
        ("ibc_inbound_atom", "IBC inbound", False),
        ("ibc_outbound_atom", "IBC outbound", False),
    ]
    values = [float(tests.loc[key, "event_percentile_empirical"]) for key, _, _ in selected]
    pvals = [float(tests.loc[key, "permutation_p_exact_plus_one"]) for key, _, _ in selected]
    labels = [label for _, label, _ in selected]
    colors = [ORANGE if sensitivity else BLUE for _, _, sensitivity in selected]

    fig, ax = plt.subplots(figsize=(7.05, 4.0), constrained_layout=True)
    y = np.arange(len(selected))
    ax.barh(y, values, color=colors, alpha=0.88)
    ax.axvline(95, color=RED, linestyle="--", linewidth=0.9)
    ax.set_yticks(y, labels=labels)
    ax.invert_yaxis()
    ax.set_xlim(0, 108)
    ax.set_xlabel("Empirical percentile among 360 non-overlapping two-hour controls")
    ax.grid(True, axis="x", linewidth=0.35, alpha=0.35)
    for yi, value, pval in zip(y, values, pvals):
        ax.text(min(value + 1.0, 101.0), yi, f"{value:.1f}; p={pval:.3f}", va="center", fontsize=7.3)
    fig.savefig(FIGURES / "event_empirical_percentiles.pdf", bbox_inches="tight")
    plt.close(fig)


def save_lead_lag() -> None:
    curves = pd.read_csv(ROOT / "results/publication_lead_lag_curves.csv")
    market_id = "1m_30d_plus_event_day_spot_usdt_return_to_spot_usdc_return"
    chain_id = "5m_30d_plus_event_day_confirmed_exchange_in_atom_to_spot_range_stress"
    market = curves[curves["analysis_id"] == market_id].sort_values("lag_minutes")
    chain = curves[curves["analysis_id"] == chain_id].sort_values("lag_minutes")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.05, 2.8), constrained_layout=True)
    ax1.plot(market["lag_minutes"], market["correlation"], color=RED, marker="o", markersize=3.0)
    ax1.axvline(0, color="black", linewidth=0.7, linestyle="--")
    ax1.axhline(0, color="0.55", linewidth=0.5)
    ax1.set_title("(a) USDT return vs. USDC return (1 min)")
    ax1.set_xlabel("Lag (min); positive: USDT leads")
    ax1.set_ylabel("Correlation")
    ax1.set_xticks(np.arange(-5, 6, 1))
    ax1.grid(True, linewidth=0.35, alpha=0.35)

    ax2.plot(chain["lag_minutes"], chain["correlation"], color=BLUE, marker="o", markersize=2.4)
    ax2.axvline(0, color="black", linewidth=0.7, linestyle="--")
    ax2.axhline(0, color="0.55", linewidth=0.5)
    ax2.set_title("(b) Confirmed inflow vs. price-range stress (5 min)")
    ax2.set_xlabel("Lag (min); positive: inflow leads")
    ax2.set_ylabel("Correlation")
    ax2.set_xticks(np.arange(-60, 61, 20))
    ax2.grid(True, linewidth=0.35, alpha=0.35)
    fig.savefig(FIGURES / "lead_lag_curves.pdf", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    configure()
    save_event_prices()
    save_sell_sweep()
    save_event_percentiles()
    save_lead_lag()
    for path in sorted(FIGURES.glob("*.pdf")):
        print(path)


if __name__ == "__main__":
    main()
