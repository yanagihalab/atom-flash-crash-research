# ATOM瞬間安値イベント：投稿用追加分析

## 結果要約

対象イベント窓は `2025-10-10T20:30:00Z` から `2025-10-10T22:30:00Z`、平時対照はイベント直前30日です。Cosmos Hub圧縮ベースライン検証は **PASS**、追加統計検証は **PASS** でした。

確認済み取引所アドレスだけのイベント流入は **188,053.420 ATOM**、未確認行動候補は **1,393,999.777 ATOM**、両者合計は **1,582,053.197 ATOM** でした。未確認候補は合計の **88.1%** を占めるため、所有者ラベルの不確実性が結論へ与える影響は小さくありません。主結果は公開ラベル確認済み4アドレスに限定します。

## イベント窓の経験的順位とプラス1補正経験的裾確率

| 指標 | イベント値 | 360窓 percentile | 360窓 p | 同時刻30窓 percentile | 同時刻30窓 p |
|---|---:|---:|---:|---:|---:|
| Binance ATOM/USDT値幅 (% of open) | 100.000 | 100.0 | 0.0028 | 100.0 | 0.0323 |
| Binance ATOM/USDT約定数 | 202,008.000 | 100.0 | 0.0028 | 100.0 | 0.0323 |
| USDT対USDC最大低値乖離 (%) | 99.937 | 100.0 | 0.0028 | 100.0 | 0.0323 |
| 確認済み取引所アドレス流入 (ATOM) | 188,053.420 | 93.9 | 0.0637 | 90.0 | 0.1290 |
| 確認済み取引所アドレス流出 (ATOM) | 195,398.333 | 94.4 | 0.0582 | 90.0 | 0.1290 |
| 確認済み取引所アドレス純流入 (ATOM) | -7,344.913 | 5.0 | 0.1191 | 3.3 | 0.1613 |
| 確認済み＋未確認候補流入 (ATOM) | 1,582,053.197 | 98.9 | 0.0139 | 93.3 | 0.0968 |
| IBC流入 (ATOM) | 277,987.973 | 98.6 | 0.0166 | 96.7 | 0.0645 |
| IBC流出 (ATOM) | 450,890.178 | 98.9 | 0.0139 | 96.7 | 0.0645 |
| IBC純流入 (ATOM) | -172,902.206 | 1.7 | 0.0249 | 3.3 | 0.0645 |

360窓は30日×12本の非重複2時間窓です。同時刻30窓は各日の20:30–22:30 UTCです。出来高・値幅・乖離・総流量は上側裾、符号付き純流入は対照中央値からの絶対偏差による両側裾を数えます。プラス1補正経験的裾確率は `(1 + exceedances)/(N + 1)`。系列依存により厳密な交換可能性は保証されず、平時分布中の稀少性を記述する経験的裾確率として解釈します。

## 1分リード・ラグ（30日＋イベント日）

| x → y | 最大\|r\|ラグ (分) | r | lag 0 r | 循環シフト p | n |
|---|---:|---:|---:|---:|---:|
| spot_usdt_return → spot_usdc_return | 0 | 0.873 | 0.873 | 0.0002 | 44,639 |
| futures_return → spot_usdt_return | 0 | 0.727 | 0.727 | 0.0002 | 44,639 |
| spot_usdt_return → mark_return | 0 | 0.795 | 0.795 | 0.0002 | 44,639 |
| spot_trade_activity → spot_range_stress | 0 | 0.056 | 0.056 | 0.0002 | 44,640 |
| usdt_vs_usdc_low_discount → spot_range_stress | 0 | 0.996 | 0.996 | 0.0002 | 44,640 |

1分分析は市場間系列（Binance現物USDT・USDC、先物、マーク価格、取引活性度・値幅）を対象とし、UTC分スロット別中央値を除去しています。

## 1分リード・ラグ（イベント局所4時間）

| x → y | 最大\|r\|ラグ (分) | r | lag 0 r | 循環シフト p | n |
|---|---:|---:|---:|---:|---:|
| spot_usdt_return → spot_usdc_return | 0 | 0.881 | 0.881 | 0.0043 | 240 |
| futures_return → spot_usdt_return | 0 | 0.699 | 0.699 | 0.0043 | 240 |
| spot_usdt_return → mark_return | 0 | 0.774 | 0.774 | 0.0043 | 240 |
| spot_trade_activity → spot_range_stress | 5 | 0.116 | 0.074 | 0.4174 | 235 |
| usdt_vs_usdc_low_discount → spot_range_stress | 0 | 0.997 | 0.997 | 0.0043 | 240 |

## 5分リード・ラグ（30日＋イベント日）

| x → y | 最大\|r\|ラグ (分) | r | lag 0 r | 循環シフト p | n |
|---|---:|---:|---:|---:|---:|
| confirmed_exchange_in_atom → spot_range_stress | -60 | 0.027 | 0.015 | 0.0602 | 8,916 |
| confirmed_exchange_out_atom → spot_range_stress | -60 | 0.040 | 0.028 | 0.0004 | 8,916 |
| unconfirmed_behavioral_in_atom → spot_range_stress | -25 | 0.028 | 0.017 | 0.1860 | 8,923 |
| all_behavioral_candidate_in_atom → spot_range_stress | -60 | 0.030 | 0.019 | 0.2969 | 8,916 |
| confirmed_exchange_net_in_atom → spot_return | -25 | 0.025 | 0.016 | 0.4199 | 8,923 |
| ibc_inbound_atom → spot_range_stress | -60 | 0.042 | 0.027 | 0.0048 | 8,916 |
| ibc_outbound_atom → spot_range_stress | -20 | 0.043 | 0.034 | 0.0146 | 8,924 |
| ibc_net_inbound_atom → spot_return | -5 | 0.066 | 0.063 | 0.0028 | 8,927 |

5分分析は確認済み取引所フロー、未確認候補を加えた感度系列、IBC送受信とBinance市場ストレス・リターンを対象とし、UTC 5分スロット別中央値を除去しています。

## 5分リード・ラグ（イベント局所4時間）

| x → y | 最大\|r\|ラグ (分) | r | lag 0 r | 循環シフト p | n |
|---|---:|---:|---:|---:|---:|
| confirmed_exchange_in_atom → spot_range_stress | 40 | -0.571 | 0.088 | 0.2083 | 40 |
| confirmed_exchange_out_atom → spot_range_stress | -40 | -0.310 | 0.037 | 0.7917 | 40 |
| unconfirmed_behavioral_in_atom → spot_range_stress | 45 | -0.506 | -0.007 | 0.7083 | 39 |
| all_behavioral_candidate_in_atom → spot_range_stress | 45 | -0.522 | 0.001 | 0.7083 | 39 |
| confirmed_exchange_net_in_atom → spot_return | -40 | 0.243 | 0.009 | 0.6667 | 40 |
| ibc_inbound_atom → spot_range_stress | 35 | -0.383 | -0.003 | 0.8750 | 41 |
| ibc_outbound_atom → spot_range_stress | 35 | -0.302 | 0.071 | 1.0000 | 41 |
| ibc_net_inbound_atom → spot_return | -30 | 0.323 | 0.131 | 0.4167 | 42 |

正のラグは x が y に先行する定義です。全報告ラグから最大絶対相関を選ぶため、循環シフト検定では各置換について同じく最大絶対相関を計算し、多重ラグ探索を反映しています。局所窓は標本数が小さく、因果方向を確定する検定ではありません。

1分の主要価格系列は最大相関がすべて lag 0 にあり、分単位の安定した市場間先行は示しません。5分の全標本では最大でも |r|=0.066 と小さく、確認済み取引所流入→市場ストレスは r=0.027、p=0.0602 でした。局所4時間の同系列は lag 40分、r=-0.571、p=0.2083 ですが、符号・ラグが全標本と一致しません。局所検定は 23 シフトのみで、p値の解像度下限は 0.0417 です。したがって、頑健なオンチェーン先行の証拠とは解釈しません。

## 頑健性と解釈

- 主分析：公開情報で確認済みの取引所アドレス4件のみ。
- 感度分析：確認済み4件に、事前に固定した behavioral-high / behavioral-medium 候補を加算。
- large-flow watchlist は取引所候補に算入しない。
- 未確認候補については、行動類似性を所有者確認とみなさない。

## Binance板情報の限界

本研究が取得したデータには、イベント直前・直後のBinance ATOM/USDTの履歴板スナップショットがありません。公開約定データは0.001 USDTで約定した事実と他市場との乖離を示しますが、直前の指値深度、注文取消、キュー滞留時間、内部清算を再構成できません。したがって、結果は **ペア固有の流動性または市場微細構造の混乱と整合的** と表現し、**流動性枯渇を証明した** とは表現しません。

## 再現性

分析JSON: `results/publication_additional_analysis.json`
Cosmos Hub検証: `results/cosmos_baseline_30d_verification.json`
追加統計検証: `results/publication_additional_verification.json`
