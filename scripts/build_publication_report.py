#!/usr/bin/env python3
"""Render the publication-extension JSON into a concise Japanese research report."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS = PROJECT_ROOT / "results"
OUTPUT = PROJECT_ROOT / "outputs" / "atom_flash_crash_2025_10_10" / "publication_additional_analysis.md"


LABELS = {
    "confirmed_exchange_in_atom": "確認済み取引所アドレス流入 (ATOM)",
    "confirmed_exchange_out_atom": "確認済み取引所アドレス流出 (ATOM)",
    "confirmed_exchange_gross_atom": "確認済み取引所アドレス総流量 (ATOM)",
    "confirmed_exchange_net_in_atom": "確認済み取引所アドレス純流入 (ATOM)",
    "unconfirmed_behavioral_in_atom": "未確認行動候補流入 (ATOM)",
    "all_behavioral_candidate_in_atom": "確認済み＋未確認候補流入 (ATOM)",
    "ibc_inbound_atom": "IBC流入 (ATOM)",
    "ibc_outbound_atom": "IBC流出 (ATOM)",
    "ibc_gross_atom": "IBC総流量 (ATOM)",
    "ibc_net_inbound_atom": "IBC純流入 (ATOM)",
    "binance_usdt_range_pct_of_open": "Binance ATOM/USDT値幅 (% of open)",
    "binance_usdt_base_volume_atom": "Binance ATOM/USDT出来高 (ATOM)",
    "binance_usdt_quote_volume": "Binance ATOM/USDT出来高 (USDT)",
    "binance_usdt_trade_count": "Binance ATOM/USDT約定数",
    "binance_usdt_realized_abs_return_pct": "Binance ATOM/USDT実現絶対変動 (%)",
    "max_usdt_vs_usdc_low_discount_pct": "USDT対USDC最大低値乖離 (%)",
    "max_usdt_vs_mark_low_discount_pct": "USDT対マーク価格最大低値乖離 (%)",
    "max_usdt_vs_coinbase_low_discount_pct": "USDT対Coinbase最大低値乖離 (%)",
}


def fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "NA"
    number = float(value)
    if not math.isfinite(number):
        return "NA"
    if abs(number) >= 1000:
        return f"{number:,.{digits}f}"
    return f"{number:.{digits}f}"


def p_fmt(value: Any) -> str:
    if value is None:
        return "NA"
    number = float(value)
    return f"{number:.4f}" if number >= 0.0001 else "<0.0001"


def selected_test_table(data: dict[str, Any], metrics: list[str]) -> list[str]:
    lines = [
        "| 指標 | イベント値 | 360窓 percentile | 360窓 p | 同時刻30窓 percentile | 同時刻30窓 p |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for metric in metrics:
        all_test = data["window_tests"][metric]["all_360"]
        matched = data["window_tests"][metric]["matched_clock_30"]
        lines.append(
            "| "
            + " | ".join(
                [
                    LABELS[metric],
                    fmt(all_test.get("event_value")),
                    fmt(all_test.get("event_percentile_empirical"), 1),
                    p_fmt(all_test.get("permutation_p_exact_plus_one")),
                    fmt(matched.get("event_percentile_empirical"), 1),
                    p_fmt(matched.get("permutation_p_exact_plus_one")),
                ]
            )
            + " |"
        )
    return lines


def lead_lag_table(rows: list[dict[str, Any]], sample: str, resolution: str) -> list[str]:
    selected = [row for row in rows if row["sample"] == sample and row["resolution"] == resolution]
    lines = [
        "| x → y | 最大|r|ラグ (分) | r | lag 0 r | 循環シフト p | n |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in selected:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"{row['x']} → {row['y']}",
                    fmt(row["max_abs_correlation_lag_minutes"], 0),
                    fmt(row["max_abs_correlation"]),
                    fmt(row["lag_zero_correlation"]),
                    p_fmt(row["circular_shift_max_lag_p"]),
                    fmt(row["paired_observations_at_best_lag"], 0),
                ]
            )
            + " |"
        )
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, default=RESULTS / "publication_additional_analysis.json")
    parser.add_argument("--verification", type=Path, default=RESULTS / "publication_additional_verification.json")
    parser.add_argument("--cosmos-verification", type=Path, default=RESULTS / "cosmos_baseline_30d_verification.json")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    data = json.loads(args.analysis.read_text(encoding="utf-8"))
    verification = json.loads(args.verification.read_text(encoding="utf-8")) if args.verification.exists() else None
    cosmos_verification = json.loads(args.cosmos_verification.read_text(encoding="utf-8"))
    robustness = data["robustness"]
    summaries = data["lead_lag"]["summaries"]
    full_five = [row for row in summaries if row["sample"] == "30d_plus_event_day" and row["resolution"] == "5m"]
    full_confirmed_in = next(row for row in full_five if row["x"] == "confirmed_exchange_in_atom")
    local_confirmed_in = next(
        row
        for row in summaries
        if row["sample"] == "event_local_4h"
        and row["resolution"] == "5m"
        and row["x"] == "confirmed_exchange_in_atom"
    )
    max_full_five_abs_r = max(abs(float(row["max_abs_correlation"])) for row in full_five)
    test_metrics = [
        "binance_usdt_range_pct_of_open",
        "binance_usdt_trade_count",
        "max_usdt_vs_usdc_low_discount_pct",
        "confirmed_exchange_in_atom",
        "confirmed_exchange_out_atom",
        "confirmed_exchange_net_in_atom",
        "all_behavioral_candidate_in_atom",
        "ibc_inbound_atom",
        "ibc_outbound_atom",
        "ibc_net_inbound_atom",
    ]
    lines = [
        "# ATOM瞬間安値イベント：投稿用追加分析",
        "",
        "## 結果要約",
        "",
        f"対象イベント窓は `{data['event_window']['start_utc']}` から `{data['event_window']['end_utc']}`、平時対照はイベント直前30日です。Cosmos Hub圧縮ベースライン検証は **{cosmos_verification['verification_status']}**、追加統計検証は **{verification['verification_status'] if verification else '未実行'}** でした。",
        "",
        f"確認済み取引所アドレスだけのイベント流入は **{fmt(robustness['confirmed_only_event_inflow_atom'])} ATOM**、未確認行動候補は **{fmt(robustness['unconfirmed_behavioral_event_inflow_atom'])} ATOM**、両者合計は **{fmt(robustness['confirmed_plus_unconfirmed_event_inflow_atom'])} ATOM** でした。未確認候補は合計の **{fmt(100 * robustness['unconfirmed_share_of_combined_event_inflow'], 1)}%** を占めるため、所有者ラベルの不確実性が結論へ与える影響は小さくありません。主結果は公開ラベル確認済み4アドレスに限定します。",
        "",
        "## イベント窓の経験的順位とプラス1補正経験的裾確率",
        "",
        *selected_test_table(data, test_metrics),
        "",
        "360窓は30日×12本の非重複2時間窓です。同時刻30窓は各日の20:30–22:30 UTCです。出来高・値幅・乖離・総流量は上側裾、符号付き純流入は対照中央値からの絶対偏差による両側裾を数えます。プラス1補正経験的裾確率は `(1 + exceedances)/(N + 1)`。系列依存により厳密な交換可能性は保証されず、平時分布中の稀少性を記述する経験的裾確率として解釈します。",
        "",
        "## 1分リード・ラグ（30日＋イベント日）",
        "",
        *lead_lag_table(data["lead_lag"]["summaries"], "30d_plus_event_day", "1m"),
        "",
        "1分分析は市場間系列（Binance現物USDT・USDC、先物、マーク価格、取引活性度・値幅）を対象とし、UTC分スロット別中央値を除去しています。",
        "",
        "## 1分リード・ラグ（イベント局所4時間）",
        "",
        *lead_lag_table(data["lead_lag"]["summaries"], "event_local_4h", "1m"),
        "",
        "## 5分リード・ラグ（30日＋イベント日）",
        "",
        *lead_lag_table(data["lead_lag"]["summaries"], "30d_plus_event_day", "5m"),
        "",
        "5分分析は確認済み取引所フロー、未確認候補を加えた感度系列、IBC送受信とBinance市場ストレス・リターンを対象とし、UTC 5分スロット別中央値を除去しています。",
        "",
        "## 5分リード・ラグ（イベント局所4時間）",
        "",
        *lead_lag_table(data["lead_lag"]["summaries"], "event_local_4h", "5m"),
        "",
        "正のラグは x が y に先行する定義です。全報告ラグから最大絶対相関を選ぶため、循環シフト検定では各置換について同じく最大絶対相関を計算し、多重ラグ探索を反映しています。局所窓は標本数が小さく、因果方向を確定する検定ではありません。",
        "",
        f"1分の主要価格系列は最大相関がすべて lag 0 にあり、分単位の安定した市場間先行は示しません。5分の全標本では最大でも |r|={fmt(max_full_five_abs_r)} と小さく、確認済み取引所流入→市場ストレスは r={fmt(full_confirmed_in['max_abs_correlation'])}、p={p_fmt(full_confirmed_in['circular_shift_max_lag_p'])} でした。局所4時間の同系列は lag {fmt(local_confirmed_in['max_abs_correlation_lag_minutes'], 0)}分、r={fmt(local_confirmed_in['max_abs_correlation'])}、p={p_fmt(local_confirmed_in['circular_shift_max_lag_p'])} ですが、符号・ラグが全標本と一致しません。局所検定は {local_confirmed_in['circular_shift_count']} シフトのみで、p値の解像度下限は {p_fmt(1 / (local_confirmed_in['circular_shift_count'] + 1))} です。したがって、頑健なオンチェーン先行の証拠とは解釈しません。",
        "",
        "## 頑健性と解釈",
        "",
        "- 主分析：公開情報で確認済みの取引所アドレス4件のみ。",
        "- 感度分析：確認済み4件に、事前に固定した behavioral-high / behavioral-medium 候補を加算。",
        "- large-flow watchlist は取引所候補に算入しない。",
        "- 未確認候補については、行動類似性を所有者確認とみなさない。",
        "",
        "## Binance板情報の限界",
        "",
        "本研究が取得したデータには、イベント直前・直後のBinance ATOM/USDTの履歴板スナップショットがありません。公開約定データは0.001 USDTで約定した事実と他市場との乖離を示しますが、直前の指値深度、注文取消、キュー滞留時間、内部清算を再構成できません。したがって、結果は **ペア固有の流動性または市場微細構造の混乱と整合的** と表現し、**流動性枯渇を証明した** とは表現しません。",
        "",
        "## 再現性",
        "",
        f"分析JSON: `{args.analysis.relative_to(PROJECT_ROOT).as_posix()}`",
        f"Cosmos Hub検証: `{args.cosmos_verification.relative_to(PROJECT_ROOT).as_posix()}`",
        f"追加統計検証: `{args.verification.relative_to(PROJECT_ROOT).as_posix()}`",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
