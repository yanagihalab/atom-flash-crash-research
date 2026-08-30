#!/usr/bin/env python3
"""Build an interactive multi-panel time-series visualization for the study."""

from __future__ import annotations

import csv
import gzip
import io
import json
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW = PROJECT_ROOT / "data" / "raw"
PROCESSED = PROJECT_ROOT / "data" / "processed" / "cosmoshub"
OUTPUT = PROJECT_ROOT / "outputs" / "atom_flash_crash_2025_10_10" / "atom_flash_crash_timeseries.html"
EVENT_START = datetime.fromisoformat("2025-10-10T20:30:00+00:00")
CONTROL_START = datetime.fromisoformat("2025-10-09T20:30:00+00:00")
WINDOW = timedelta(hours=2)
FLASH = datetime.fromisoformat("2025-10-10T21:20:37.689043+00:00")


def epoch_to_us(value: str | int) -> int:
    number = int(value)
    if number >= 10**15:
        return number
    if number >= 10**12:
        return number * 1000
    return number * 1_000_000


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def read_klines(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(path) as archive, archive.open(archive.namelist()[0]) as binary:
        for raw in csv.reader(io.TextIOWrapper(binary, encoding="utf-8")):
            if not raw or raw[0] == "open_time":
                continue
            rows.append(
                {
                    "time": datetime.fromtimestamp(epoch_to_us(raw[0]) / 1_000_000, timezone.utc),
                    "low": float(Decimal(raw[3])),
                    "close": float(Decimal(raw[4])),
                    "trades": int(raw[8]),
                }
            )
    return rows


def read_kraken(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            raw = json.loads(line)
            rows.append({"time": parse_time(raw["trade_ts"]), "price": float(Decimal(raw["price"]))})
    return rows


def read_coinbase(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [
        {
            "time": datetime.fromtimestamp(int(row[0]), timezone.utc),
            "low": float(row[1]),
            "close": float(row[4]),
        }
        for row in rows
    ]


def kline_window(rows: list[dict[str, Any]], start: datetime) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    by_minute = {int((row["time"] - start).total_seconds() // 60): row for row in rows if start <= row["time"] < start + WINDOW}
    low = [{"x": minute + 0.5, "y": by_minute[minute]["low"]} for minute in range(120) if minute in by_minute]
    trades = [{"x": minute + 0.5, "y": by_minute[minute]["trades"]} for minute in range(120) if minute in by_minute]
    return low, trades


def sparse_price_window(rows: list[dict[str, Any]], start: datetime, price_key: str) -> list[dict[str, float]]:
    minute_low: dict[int, float] = {}
    last_before: float | None = None
    for row in sorted(rows, key=lambda item: item["time"]):
        price = float(row[price_key])
        if row["time"] < start:
            last_before = price
            continue
        if row["time"] >= start + WINDOW:
            break
        minute = int((row["time"] - start).total_seconds() // 60)
        minute_low[minute] = min(minute_low.get(minute, price), price)
    values: list[dict[str, float]] = []
    last = last_before
    for minute in range(120):
        if minute in minute_low:
            last = minute_low[minute]
        if last is not None:
            values.append({"x": minute + 0.5, "y": last})
    return values


def jsonl_gzip(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def chain_windows() -> dict[str, list[dict[str, float]]]:
    candidates = json.loads(
        (PROCESSED / "exchange_inflow_candidates_2025-10-09_2025-10-12.json").read_text(encoding="utf-8")
    )["candidates"]
    structured = {row["address"] for row in candidates if row["candidate_tier"] in {"behavioral_high", "behavioral_medium"}}
    labels = json.loads((PROJECT_ROOT / "metadata" / "exchange_address_labels.json").read_text(encoding="utf-8"))["labels"]
    labeled = {row["address"] for row in labels}
    starts = {"Event": EVENT_START, "Pre-day": CONTROL_START}
    buckets = {
        name: {
            "direct": defaultdict(int),
            "structured": defaultdict(int),
            "cex_net": defaultdict(int),
            "ibc_net": defaultdict(int),
        }
        for name in starts
    }

    for row in jsonl_gzip(PROCESSED / "atom_transfers_2025-10-09_2025-10-12.jsonl.gz"):
        if row["flow_class"] != "direct_bank":
            continue
        timestamp = parse_time(row["time_utc"])
        for name, start in starts.items():
            if start <= timestamp < start + WINDOW:
                bucket = int((timestamp - start).total_seconds() // 300)
                amount = int(row["amount_uatom"])
                buckets[name]["direct"][bucket] += amount
                if row["recipient"] in structured:
                    buckets[name]["structured"][bucket] += amount
                if row["recipient"] in labeled:
                    buckets[name]["cex_net"][bucket] += amount
                if row["sender"] in labeled:
                    buckets[name]["cex_net"][bucket] -= amount

    for row in jsonl_gzip(PROCESSED / "ibc_transfers_2025-10-09_2025-10-12.jsonl.gz"):
        if not row["is_atom"]:
            continue
        timestamp = parse_time(row["time_utc"])
        for name, start in starts.items():
            if start <= timestamp < start + WINDOW:
                bucket = int((timestamp - start).total_seconds() // 300)
                sign = 1 if row["direction"] == "inbound" else -1
                buckets[name]["ibc_net"][bucket] += sign * int(row["amount_base_units"])

    output: dict[str, list[dict[str, float]]] = {}
    for name in starts:
        for metric in ("direct", "structured", "cex_net", "ibc_net"):
            output[f"{metric}_{name.lower().replace('-', '_')}"] = [
                {"x": bucket * 5 + 2.5, "y": buckets[name][metric][bucket] / 1_000_000}
                for bucket in range(24)
            ]
    return output


def build_data() -> dict[str, Any]:
    day = "2025-10-10"
    pre_day = "2025-10-09"
    event_paths = {
        "Binance ATOM/USDT": RAW / "binance/spot/daily/klines/ATOMUSDT/1m" / f"ATOMUSDT-1m-{day}.zip",
        "Binance ATOM/USDC": RAW / "binance/spot/daily/klines/ATOMUSDC/1m" / f"ATOMUSDC-1m-{day}.zip",
        "Binance futures": RAW / "binance/futures/um/daily/klines/ATOMUSDT/1m" / f"ATOMUSDT-1m-{day}.zip",
        "Binance mark": RAW / "binance/futures/um/daily/markPriceKlines/ATOMUSDT/1m" / f"ATOMUSDT-1m-{day}.zip",
    }
    event_klines = {name: read_klines(path) for name, path in event_paths.items()}
    event_price = []
    colors = [1, 2, 3, 4, 5, 6]
    for color, (name, rows) in zip(colors, event_klines.items()):
        lows, _ = kline_window(rows, EVENT_START)
        event_price.append({"name": name, "color": color, "values": lows})

    kraken = read_kraken(RAW / "kraken/spot/trades/ATOMUSD" / f"ATOMUSD-trades-{day}.jsonl.gz")
    coinbase = read_coinbase(RAW / "coinbase/spot/candles/ATOM-USD/60s" / f"ATOM-USD-60s-{day}.json")
    event_price.append({"name": "Kraken ATOM/USD", "color": 5, "values": sparse_price_window(kraken, EVENT_START, "price")})
    event_price.append({"name": "Coinbase ATOM/USD", "color": 6, "values": sparse_price_window(coinbase, EVENT_START, "low")})

    event_usdt = event_klines["Binance ATOM/USDT"]
    control_usdt = read_klines(RAW / "binance/spot/daily/klines/ATOMUSDT/1m" / f"ATOMUSDT-1m-{pre_day}.zip")
    _, event_trades = kline_window(event_usdt, EVENT_START)
    _, control_trades = kline_window(control_usdt, CONTROL_START)
    chain = chain_windows()

    return {
        "eventMinute": (FLASH - EVENT_START).total_seconds() / 60,
        "panels": [
            {
                "id": "price",
                "title": "Cross-venue minute lows during the event window",
                "yTitle": "Price (USD-equivalent, log scale)",
                "scale": "log",
                "format": ".4~g",
                "series": event_price,
            },
            {
                "id": "trades",
                "title": "Binance ATOM/USDT trades per minute",
                "yTitle": "Trade count (log scale)",
                "scale": "log",
                "format": ",.0f",
                "series": [
                    {"name": "Event", "color": 1, "values": event_trades},
                    {"name": "Pre-day matched", "color": 1, "dash": "6 4", "values": control_trades},
                ],
            },
            {
                "id": "gross",
                "title": "Cosmos Hub gross movement per 5 minutes",
                "yTitle": "ATOM per 5 min",
                "scale": "linear",
                "format": ",.0f",
                "series": [
                    {"name": "Direct — event", "color": 1, "values": chain["direct_event"]},
                    {"name": "Direct — pre-day", "color": 1, "dash": "6 4", "values": chain["direct_pre_day"]},
                    {"name": "Structured in — event", "color": 3, "values": chain["structured_event"]},
                    {"name": "Structured in — pre-day", "color": 3, "dash": "6 4", "values": chain["structured_pre_day"]},
                ],
            },
            {
                "id": "net",
                "title": "Cosmos Hub net flow per 5 minutes",
                "yTitle": "Net inbound ATOM (symlog scale)",
                "scale": "symlog",
                "format": ",.0f",
                "series": [
                    {"name": "CEX net — event", "color": 2, "values": chain["cex_net_event"]},
                    {"name": "CEX net — pre-day", "color": 2, "dash": "6 4", "values": chain["cex_net_pre_day"]},
                    {"name": "IBC net — event", "color": 4, "values": chain["ibc_net_event"]},
                    {"name": "IBC net — pre-day", "color": 4, "dash": "6 4", "values": chain["ibc_net_pre_day"]},
                ],
            },
        ],
    }


def validate_data(data: dict[str, Any]) -> None:
    assert 0 < data["eventMinute"] < 120
    assert len(data["panels"]) == 4
    for panel in data["panels"]:
        assert panel["series"]
        for series in panel["series"]:
            assert series["values"]
            assert all(0 <= row["x"] <= 120 for row in series["values"])
            assert all(isinstance(row["y"], (int, float)) for row in series["values"])
    price_panel = next(panel for panel in data["panels"] if panel["id"] == "price")
    binance = next(series for series in price_panel["series"] if series["name"] == "Binance ATOM/USDT")
    assert min(row["y"] for row in binance["values"]) == 0.001


def build_fragment(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f'''<div id="atom-ts-viz">
  <h1>ATOM flash-crash time series</h1>
  <div class="plot-grid" aria-label="Four synchronized time-series panels"></div>
  <div class="tooltip" role="tooltip" hidden></div>
</div>
<style>
#atom-ts-viz {{ position: relative; width: 100%; color: var(--foreground); }}
#atom-ts-viz h1 {{ margin: 0 0 12px; font-weight: 500; }}
#atom-ts-viz .plot-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px 22px; }}
#atom-ts-viz .plot {{ min-width: 0; }}
#atom-ts-viz .plot h2 {{ margin: 0 0 4px; font-weight: 500; }}
#atom-ts-viz .legend {{ display: flex; flex-wrap: wrap; gap: 2px 12px; margin: 0 0 4px; }}
#atom-ts-viz .legend button {{ appearance: none; background: transparent; border: 0; color: var(--foreground); padding: 3px 0; font: inherit; cursor: pointer; }}
#atom-ts-viz .legend button[aria-pressed="false"] {{ color: var(--muted-foreground); text-decoration: line-through; }}
#atom-ts-viz .swatch {{ display: inline-block; width: 18px; height: 3px; margin-right: 5px; vertical-align: middle; background: var(--swatch); }}
#atom-ts-viz .swatch.dashed {{ background: repeating-linear-gradient(90deg, var(--swatch) 0 7px, transparent 7px 11px); }}
#atom-ts-viz svg {{ display: block; width: 100%; overflow: visible; }}
#atom-ts-viz .axis text, #atom-ts-viz .axis-title, #atom-ts-viz .event-label {{ fill: var(--foreground); font-size: 12px; }}
#atom-ts-viz .axis path, #atom-ts-viz .axis line {{ stroke: var(--border); }}
#atom-ts-viz .gridline line {{ stroke: var(--border); stroke-opacity: .45; }}
#atom-ts-viz .gridline path {{ display: none; }}
#atom-ts-viz [data-chart-frame] {{ fill: transparent; stroke: var(--border); }}
#atom-ts-viz [data-chart-hover-guide] {{ stroke: var(--foreground); stroke-opacity: .5; pointer-events: none; }}
#atom-ts-viz .event-line {{ stroke: var(--destructive); stroke-width: 1.5; stroke-dasharray: 4 4; }}
#atom-ts-viz .series-line {{ fill: none; stroke-width: 2; }}
#atom-ts-viz .tooltip {{ position: absolute; z-index: 10; pointer-events: none; max-width: 300px; padding: 8px 10px; background: var(--popover); color: var(--popover-foreground); border: 1px solid var(--border); }}
#atom-ts-viz .tooltip-row {{ display: flex; justify-content: space-between; gap: 16px; }}
#atom-ts-viz .tooltip-key {{ white-space: nowrap; }}
@media (max-width: 720px) {{ #atom-ts-viz .plot-grid {{ grid-template-columns: 1fr; }} }}
</style>
<script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"></script>
<script>
(() => {{
  const root = document.getElementById("atom-ts-viz");
  const data = {payload};
  const grid = root.querySelector(".plot-grid");
  const tooltip = root.querySelector(".tooltip");
  const color = n => `var(--viz-series-${{n}})`;
  const state = new Map();

  function interpolate(values, x) {{
    if (!values.length || x < values[0].x || x > values[values.length - 1].x) return null;
    const i = d3.bisector(d => d.x).left(values, x);
    if (i === 0) return values[0].y;
    if (i >= values.length) return values[values.length - 1].y;
    const a = values[i - 1], b = values[i];
    const t = (x - a.x) / (b.x - a.x || 1);
    return a.y + (b.y - a.y) * t;
  }}

  function scaleFor(panel, extent, range) {{
    if (panel.scale === "log") {{
      const positive = extent.filter(v => v > 0);
      return d3.scaleLog().domain([d3.min(positive) * .82, d3.max(positive) * 1.18]).range(range).nice();
    }}
    if (panel.scale === "symlog") {{
      const maxAbs = d3.max(extent, Math.abs) || 1;
      return d3.scaleSymlog().constant(1000).domain([-maxAbs * 1.12, maxAbs * 1.12]).range(range).nice();
    }}
    const lo = Math.min(0, d3.min(extent));
    const hi = d3.max(extent) || 1;
    return d3.scaleLinear().domain([lo, hi * 1.08]).range(range).nice();
  }}

  function draw(panel, holder) {{
    holder.querySelectorAll("svg").forEach(node => node.remove());
    const width = Math.max(320, Math.floor(holder.getBoundingClientRect().width));
    const height = 330;
    const margin = {{ top: 14, right: 16, bottom: 54, left: width < 440 ? 70 : 78 }};
    const innerW = width - margin.left - margin.right;
    const innerH = height - margin.top - margin.bottom;
    const svg = d3.select(holder).append("svg").attr("viewBox", `0 0 ${{width}} ${{height}}`).attr("aria-label", panel.title);
    const g = svg.append("g").attr("transform", `translate(${{margin.left}},${{margin.top}})`);
    const visible = panel.series.filter(series => state.get(panel.id + series.name) !== false);
    const allValues = visible.flatMap(series => series.values.map(d => d.y));
    const x = d3.scaleLinear().domain([0, 120]).range([0, innerW]);
    const y = scaleFor(panel, allValues.length ? allValues : [0, 1], [innerH, 0]);
    const scaledValue = value => panel.scale === "log" ? Math.max(y.domain()[0], value) : value;
    const ticks = width < 500 ? 4 : 7;
    g.append("g").attr("class", "gridline").call(d3.axisLeft(y).ticks(5).tickSize(-innerW).tickFormat(""));
    g.append("rect").attr("data-chart-frame", "").attr("width", innerW).attr("height", innerH);
    g.append("g").attr("class", "axis").attr("transform", `translate(0,${{innerH}})`).call(d3.axisBottom(x).ticks(ticks));
    g.append("g").attr("class", "axis").call(d3.axisLeft(y).ticks(5, panel.format));
    g.append("text").attr("class", "axis-title").attr("data-axis", "x").attr("x", innerW / 2).attr("y", innerH + 44).attr("text-anchor", "middle").text("Minutes since 20:30 UTC");
    g.append("text").attr("class", "axis-title").attr("data-axis", "y").attr("transform", "rotate(-90)").attr("x", -innerH / 2).attr("y", -58).attr("text-anchor", "middle").text(panel.yTitle);
    g.append("line").attr("class", "event-line").attr("x1", x(data.eventMinute)).attr("x2", x(data.eventMinute)).attr("y1", 0).attr("y2", innerH);
    g.append("text").attr("class", "event-label").attr("x", Math.min(innerW - 4, x(data.eventMinute) + 5)).attr("y", 13).text("Flash 21:20:37 UTC");

    const line = d3.line().x(d => x(d.x)).y(d => y(scaledValue(d.y))).defined(d => Number.isFinite(d.y));
    for (const series of visible) {{
      g.append("path").datum(series.values).attr("class", "series-line").attr("stroke", color(series.color)).attr("stroke-dasharray", series.dash || null).attr("d", line);
    }}

    const guide = g.append("line").attr("data-chart-hover-guide", "").attr("y1", 0).attr("y2", innerH).style("display", "none");
    const markerLayer = g.append("g");
    const overlay = g.append("rect").attr("data-chart-hit", "").attr("data-chart-hover-overlay", "cross-series").attr("width", innerW).attr("height", innerH).attr("fill", "transparent").style("pointer-events", "all");

    function update(event, pin = false) {{
      const [px] = d3.pointer(event, overlay.node());
      const xv = Math.max(0, Math.min(120, x.invert(px)));
      guide.attr("x1", x(xv)).attr("x2", x(xv)).style("display", null);
      markerLayer.selectAll("*").remove();
      const rows = [];
      for (const series of visible) {{
        const value = interpolate(series.values, xv);
        if (value == null || !Number.isFinite(value)) continue;
        markerLayer.append("circle").attr("data-chart-hover-marker", "").attr("cx", x(xv)).attr("cy", y(scaledValue(value))).attr("r", 4).attr("fill", color(series.color));
        rows.push(`<div class="tooltip-row"><span class="tooltip-key">${{series.name}}</span><span>${{d3.format(panel.format)(value)}}</span></div>`);
      }}
      tooltip.innerHTML = `<div class="tooltip-row"><strong>Minute</strong><strong>${{d3.format(".1f")(xv)}}</strong></div>${{rows.join("")}}`;
      tooltip.hidden = false;
      const rootBox = root.getBoundingClientRect();
      const svgBox = svg.node().getBoundingClientRect();
      tooltip.style.left = `${{Math.min(rootBox.width - 300, svgBox.left - rootBox.left + margin.left + x(xv) + 12)}}px`;
      tooltip.style.top = `${{svgBox.top - rootBox.top + margin.top + 8}}px`;
      if (pin) tooltip.dataset.pinned = "true";
    }}
    overlay.on("pointermove", event => {{ if (tooltip.dataset.pinned !== "true") update(event); }}).on("pointerleave", () => {{
      if (tooltip.dataset.pinned !== "true") {{ guide.style("display", "none"); markerLayer.selectAll("*").remove(); tooltip.hidden = true; }}
    }}).on("click", event => {{ delete tooltip.dataset.pinned; update(event, true); }});
  }}

  for (const panel of data.panels) {{
    const section = document.createElement("section");
    section.className = "plot";
    const heading = document.createElement("h2");
    heading.textContent = panel.title;
    const legend = document.createElement("div");
    legend.className = "legend";
    const canvas = document.createElement("div");
    section.append(heading, legend, canvas);
    grid.append(section);
    for (const series of panel.series) {{
      state.set(panel.id + series.name, true);
      const button = document.createElement("button");
      button.type = "button";
      button.setAttribute("aria-pressed", "true");
      const swatch = document.createElement("span");
      swatch.className = "swatch" + (series.dash ? " dashed" : "");
      swatch.style.setProperty("--swatch", color(series.color));
      button.append(swatch, document.createTextNode(series.name));
      button.addEventListener("click", () => {{
        const key = panel.id + series.name;
        const next = !state.get(key);
        state.set(key, next);
        button.setAttribute("aria-pressed", String(next));
        draw(panel, canvas);
      }});
      legend.append(button);
    }}
    draw(panel, canvas);
    let lastWidth = Math.round(canvas.getBoundingClientRect().width);
    new ResizeObserver(entries => {{
      const nextWidth = Math.round(entries[0].contentRect.width);
      if (nextWidth > 0 && nextWidth !== lastWidth) {{
        lastWidth = nextWidth;
        draw(panel, canvas);
      }}
    }}).observe(canvas);
  }}
}})();
</script>
'''


def main() -> None:
    data = build_data()
    validate_data(data)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fragment = build_fragment(data)
    assert len(fragment.encode("utf-8")) < 1_000_000
    assert "<html" not in fragment.lower() and "<!doctype" not in fragment.lower()
    OUTPUT.write_text(fragment, encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "bytes": OUTPUT.stat().st_size, "panels": len(data["panels"]), "binance_min": 0.001}))


if __name__ == "__main__":
    main()
