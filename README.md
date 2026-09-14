# Analysis-Ready Intermediate Data for a Transient Price Dislocation in the Binance ATOM/USDT Market

Binance ATOM/USDT市場における瞬間的価格乖離の分析用中間データ

[English](#english) | [日本語](#japanese)

---

<a id="english"></a>

## English

### Overview

This repository contains analysis-ready intermediate data, analysis code,
verification outputs, and reproducibility documentation for the 0.001 USDT
execution observed in the Binance ATOM/USDT spot market at
2025-10-10T21:20:37.689043Z.

The study integrates public executions, Binance comparison pairs and futures
reference series, Coinbase and Kraken ATOM/USD observations, and Cosmos Hub ATOM
transfer and IBC events. It examines:

- how extreme the event window was relative to a 30-day pre-event baseline;
- one-minute and five-minute lead-lag relationships across market and on-chain series;
- robustness when only publicly confirmed exchange addresses are used;
- sensitivity to adding unconfirmed behavior-based candidates; and
- whether public data can attribute deliberate action to a wallet or user.

### Scope of the claims

> The evidence is consistent with a pair-specific liquidity or market-microstructure
> disruption. It does not prove liquidity depletion, a causal link to a particular
> wallet, deliberate manipulation, or the involvement of a named person. Historical
> Binance order-book snapshots, internal account identifiers, order identifiers,
> and deposit-credit timestamps are unavailable.

A blockchain address is a public-ledger identifier, not a natural person or
beneficial owner. An exchange label indicates only an exact-address association
supported by a retained public record.

### Publication policy

The GitHub publication unit is an analysis-ready intermediate dataset, not a
mirror of third-party raw source archives.

| Category | Tracked in Git | Description |
|---|---:|---|
| Analysis-ready intermediate data | Yes | Event extract, ATOM transfers, IBC, 30-day five-minute panel, block-time index |
| Statistical analysis tables | Yes | 360 control windows, empirical tests, lead-lag curves, robustness comparisons |
| Analysis and validation code | Yes | Acquisition, extraction, aggregation, testing, validation, and figure scripts |
| Provenance and integrity metadata | Yes | Sources, run records, SHA-256 inventory, and validation manifests |
| Binance, Coinbase, and Kraken raw data | No | Acquisition scripts and run records are included |
| Complete Cosmos Hub RPC responses | No | Acquisition/verification scripts and processed manifests are included |
| Historical Binance order book | No | No event-time historical depth snapshot was available |

The corrected dataset (v0.1.1, 15 September 2026) contains 385 intermediate data files totaling 67,614,461 bytes.
See [metadata/file_inventory.csv](metadata/file_inventory.csv) for file-level
SHA-256 digests and
[metadata/dataset_summary.json](metadata/dataset_summary.json) for the summary.

### Time coverage and analysis units

All analysis timestamps are UTC.

| Component | Interval or timestamp |
|---|---|
| Observed low | 2025-10-10T21:20:37.689043Z |
| Detailed trade extract | [2025-10-10T21:15:00Z, 21:25:00Z) |
| Primary event window | [2025-10-10T20:30:00Z, 22:30:00Z) |
| Detailed Cosmos Hub interval | 9–12 October 2025 |
| 30-day pre-event baseline | [2025-09-10T00:00:00Z, 2025-10-10T00:00:00Z) |
| Main control distribution | 360 non-overlapping two-hour windows |
| Matched-clock sensitivity set | 30 windows at 20:30–22:30 UTC |
| Full lead-lag sample | 31 UTC days, 10 September–10 October 2025 inclusive |
| Lead-lag ranges | ±5 minutes at 1-minute resolution; ±60 minutes at 5-minute resolution |

The event's one-minute figures contain exactly 120 bars in the half-open
20:30–22:30 UTC interval. Each circular-shift null uses the same lag-trimmed,
finite-pair Pearson statistic as the observed series, without closing missing
time gaps: 5,000 shifts for the full sample and all 23 eligible shifts for the
local four-hour five-minute sample.

### Main intermediate artifacts

- [Ten-minute ATOM/USDT trade extract](data/processed/event_window/binance_spot_atomusdt_trades_2025-10-10_2115-2125_utc.csv.gz)
- [Normalized ATOM transfers](data/processed/cosmoshub/atom_transfers_2025-10-09_2025-10-12.jsonl.gz)
- [Decoded IBC sends and receives](data/processed/cosmoshub/ibc_transfers_2025-10-09_2025-10-12.jsonl.gz)
- [Combined event-window flows](data/processed/cosmoshub/event_window_flows_2025-10-10_2030-2230_utc.jsonl.gz)
- [Exchange-inflow candidate registry](data/processed/cosmoshub/exchange_inflow_candidates_2025-10-09_2025-10-12.json)
- [Continuous 30-day five-minute on-chain panel](data/processed/cosmoshub/baseline_30d/baseline_5min_2025-09-10_2025-10-10.jsonl.gz)
- [Compact 30-day block-time index](data/processed/cosmoshub/baseline_30d/block_times_2025-09-10_2025-10-10.jsonl.gz)
- [Thirty-day receipt-policy audit](data/processed/cosmoshub/baseline_30d/ibc_receive_policy_audit.json)
- [Compact receipt evidence](data/processed/cosmoshub/baseline_30d/ibc_receive_evidence.jsonl.gz) and [transaction index](data/processed/cosmoshub/baseline_30d/ibc_raw_transaction_index.jsonl.gz)

See [data/processed/README.md](data/processed/README.md) for the intermediate-data
contract and [docs/data_dictionary.md](docs/data_dictionary.md) for field definitions.

IBC inbound flow follows `native-atom-receipt-success-v2`: a successful relay
transaction alone is insufficient. An exact native-ATOM return trace, matching
application-success event and native credit are required. Valid packet-forward
middleware (PFM) credits with deferred acknowledgements remain included. Outbound
flow measures successful Hub send initiation, not final remote settlement.

Reacquiring and re-auditing all 30 baseline days excluded 20 legacy candidates:
18 application failures and two non-native return traces, leaving 57,303 eligible
native receipts. The four-day detailed data separately exclude 10 failed receipts;
these intervals overlap and their exclusion counts must not be added. The event
window's 1,113 native receipts are unchanged, including 583 with deferred ACKs.

### Main analysis tables

| File | Contents |
|---|---|
| [publication_control_windows.csv](results/publication_control_windows.csv) | 360 non-overlapping two-hour controls |
| [publication_matched_clock_windows.csv](results/publication_matched_clock_windows.csv) | 30 matched-clock controls |
| [publication_window_tests.csv](results/publication_window_tests.csv) | Empirical percentiles and plus-one empirical tail probabilities |
| [publication_lead_lag.csv](results/publication_lead_lag.csv) | 26 lead-lag summaries |
| [publication_lead_lag_curves.csv](results/publication_lead_lag_curves.csv) | All 510 evaluated lag points |
| [wallet_coordination_candidates.csv](results/wallet_coordination_candidates.csv) | Descriptive pre-event exchange-inflow sender candidates |
| [flash_sell_sequence.csv](results/flash_sell_sequence.csv) | Sell-aggressor sequence at the final-low microsecond |

The files in results are both manuscript-verification outputs and tidy inputs for
additional statistical analysis or visualization.

### Quick start

Python 3.11 or later is recommended.

~~~bash
git clone https://github.com/yanagihalab/atom-flash-crash-research.git
cd atom-flash-crash-research

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

make validate
make verify
~~~

`make validate` checks tracked and non-ignored untracked files, JSON syntax,
gzip/ZIP streams, individual file size, local absolute paths, and token-like secrets.
`make verify` checks the saved publication tables and runs explicit processed-only
wallet verification, including processed-source hashes and reported wallet metrics.
Neither command requires the excluded raw files. The processed-only report records
that raw market hashes, 92 raw fills and 71 aggregate-trade rows are not rechecked.

The complete 30-day compact bundle and four-day flows can also be checked offline:

~~~bash
python scripts/verify_cosmos_flows.py
python scripts/verify_cosmos_baseline_indexed.py
~~~

`make verify-raw` separately requires locally acquired raw market files and includes
full wallet and independent direct-Pearson circular-shift verification. It does
not silently fall back to processed-only verification when raw inputs are missing.

### Analysis from the intermediate files

Immediately after cloning, users can:

- reaggregate ATOM transfers, IBC activity, and confirmed exchange flows;
- recalculate control-window tests and inspect lead-lag results;
- compare the confirmed-address primary analysis with the unconfirmed-candidate sensitivity analysis;
- reassess address concentration and temporal proximity; and
- use the included PDF figures and Japanese analysis reports in manuscript workflows.

See [docs/reproducibility.md](docs/reproducibility.md) for the full workflow.

### Full rebuild from raw sources

Raw inputs are downloaded to the Git-ignored data/raw directory. Users must
review third-party availability, terms, and possible future API changes.

~~~bash
python scripts/download_binance.py
python scripts/download_comparison_markets.py
python scripts/collect_cosmos_study_data.py
~~~

After acquisition, using the verified published 30-day bundle:

~~~bash
python scripts/build_event_extract.py
python scripts/extract_cosmos_flows.py
python scripts/compare_control_windows.py
python scripts/analyze_publication_extensions.py
python scripts/analyze_wallet_coordination.py
~~~

These default downloads do not alone recreate the extended 30-day market inputs;
use the baseline acquisition configuration described in
[docs/reproducibility.md](docs/reproducibility.md). The indexed collector alone
does not produce the full v2 audit/evidence/snapshot bundle. Raw receipt re-extraction
from a preserved legacy baseline uses the separate migration procedure in that
guide. Do not overwrite or certify a baseline by adding only a policy string.

Archive RPC availability may change. Sources and recorded acquisition runs are
documented in [metadata/source_registry.json](metadata/source_registry.json)
and [metadata/runs/](metadata/runs/).

### Main reproducible findings

For window comparisons, `p` denotes a descriptive plus-one empirical tail
probability. Serial dependence and overlapping comparison sets preclude assuming
strict exchangeability. Circular-shift p-values use a separate, explicitly defined null.

- The ATOM/USDT two-hour event-window price range, trade count, and low-price
  dislocation relative to ATOM/USDC exceed all 360 pre-event control windows.
- Inflow to exact-address, publicly confirmed exchange labels is at the 93.9th
  percentile with plus-one p=0.0637; its descriptive tail probability exceeds 0.05.
- Correcting baseline IBC receipts leaves all reported IBC event percentiles and
  plus-one p-values unchanged. Inbound activity remains at the 98.6th percentile
  (p=0.0166) among 360 controls; its 30 matched-clock controls give p=0.0645.
- The primary full-sample five-minute exchange-inflow/stress test has r=0.0274
  at lag −60 minutes and corrected max-lag p=0.0602. The local four-hour sensitivity
  test has p=0.2083; neither result establishes causal direction.
- The terminal low was formed by 92 consecutive sell-aggressor executions in the
  same microsecond, totaling 9,695.33 ATOM.
- A temporally nearby on-chain deposit is observable, but public data contain no
  exchange-internal join key connecting it to the sell sequence or a user.
- The nearest pre-low sender's 10 distinct memo clusters refer specifically to
  its 59 Binance-bound transfers, not to all of that sender's outgoing transfers.
- Adding unconfirmed candidates does not establish wallet-specific manipulation.

These are descriptive and statistical findings within the dataset. They do not
establish causality, intent, or wrongdoing.

### Integrity and provenance

- Binance archives were checked against provider .CHECKSUM SHA-256 values.
- Cosmos Hub acquisition was checked for continuous heights, UTC boundaries,
  pagination, and transaction counts.
- Selected blocks and result hashes were cross-checked across independent RPC endpoints.
- Intermediate files are fixed by the [SHA-256 inventory](metadata/file_inventory.csv).
- The 36 publication-test rows are checked for CSV/JSON consistency; the 26 lead-lag summaries are checked against extrema in the 510 saved curve rows.
- The routine check recalculates ranks and tail probabilities from the 360 controls. For the 30 time-matched controls it checks saved CSV/JSON agreement, without independently recalculating the control distribution or circular-shift nulls.
- A separate raw-dependent direct-Pearson verifier independently recalculates the circular-shift nulls for the primary exchange-inflow/stress series in the full sample and local four-hour sample only.
- The absence of historical order-book data and the permitted scope of claims are machine-checked.

Final checks are recorded in
[publication_additional_verification.json](results/publication_additional_verification.json)
and [wallet_coordination_verification.json](results/wallet_coordination_verification.json).
The separate [processed-only wallet report](results/wallet_coordination_processed_verification.json)
states its narrower scope. [Direct-Pearson verification](results/publication_circular_shift_verification.json)
records the retained-raw check of all primary shifts. The
[packaging provenance map](metadata/publication_packaging_manifest.json) retains
original source/report hashes and explicit public-artifact SHA rebinding; original
raw and extraction-code provenance is not replaced by later packaging hashes.

### Repository layout

~~~text
config/          Acquisition, event-window, and baseline configuration
data/processed/  Analysis-ready intermediate data tracked in Git
docs/            Data dictionary, reproducibility guide, and ethics guidance
figures/         Reproducible PDF figures for the manuscript
metadata/        Sources, acquisition records, labels, and SHA-256 inventory
outputs/         Japanese analysis reports and analysis workbook
results/         Aggregates, tests, lead-lag tables, and verification outputs
scripts/         Acquisition, extraction, analysis, validation, and figure code
~~~

### Limitations and ethics

- No historical Binance ATOM/USDT order-book snapshot is available; resting
  depth, cancellations, queue age, and actual liquidity depletion cannot be reconstructed.
- Public executions contain no user ID, account ID, common taker-order ID,
  API-key audit information, or liquidation flag.
- A Cosmos Hub transfer timestamp is not an exchange deposit-credit timestamp.
- IBC direction is defined from the Cosmos Hub perspective and is not centralized-exchange net flow.
- Behavior-based candidates are not ownership labels and are used only for sensitivity analysis.
- Addresses and memos must not be used to identify a person or assert misconduct.

See [docs/ETHICS.md](docs/ETHICS.md) for the complete guidance.

### License and citation

- Code: [MIT License](LICENSE)
- Author-created documentation, figures, intermediate data, and analysis outputs:
  [CC BY 4.0](LICENSE-DATA.md)
- Third-party raw source data: not included and not relicensed
- Citation metadata: [CITATION.cff](CITATION.cff)

Please include the release version or commit hash when citing the dataset or
reporting a secondary analysis.

---

<a id="japanese"></a>

## 日本語

### 概要

本リポジトリは、2025年10月10日21:20:37.689043 UTC
（日本時間2025年10月11日06:20:37.689043）にBinanceのATOM/USDT現物市場で
観測された0.001 USDTの約定を検証するための、分析用中間データ、分析コード、
検証結果及び再現手順を収録しています。

公開約定データ、Binanceの比較ペア・先物参照系列、Coinbase及びKrakenの
ATOM/USD系列、Cosmos HubのATOM移転・IBCイベントを統合し、次を検討します。

- イベント窓が直前30日の平時対照分布に対してどの程度極端だったか
- 現物・先物・比較市場・オンチェーン系列の1分及び5分リード・ラグ関係
- 確認済み取引所アドレスだけを用いた場合の頑健性
- 未確認の行動ベース候補を含めた感度分析との差
- 特定ウォレット又は利用者による意図的操作を公開情報から帰属できるか

### 研究上の主張範囲

> 本データが支持するのは、観測された約定パターンがペア固有の流動性又は
> 市場マイクロ構造の混乱と整合することまでです。Binanceの履歴板、
> 内部口座ID、注文ID及び入金反映時刻が存在しないため、流動性枯渇そのもの、
> 特定ウォレットとの因果関係、意図的操作又は自然人の関与は証明できません。

アドレスは公開台帳上の識別子であり、自然人や実質的所有者を表すものではありません。
取引所ラベルも、公開記録との完全一致が示すサービスとの関係だけを意味します。

### データ公開方針

GitHubで公開するデータ単位は、原データではなく、集約・統計解析へ直接入力できる
中間ファイルです。

| 区分 | Git収録 | 説明 |
|---|---:|---|
| 分析用中間データ | 収録 | 約定イベント抽出、ATOM移転、IBC、30日5分集計、ブロック時刻索引 |
| 統計解析用テーブル | 収録 | 360対照窓、経験的検定、リード・ラグ曲線、頑健性比較 |
| 検証・分析コード | 収録 | 取得、抽出、集計、検定、検証、作図スクリプト |
| 来歴・完全性情報 | 収録 | 取得元、実行記録、SHA-256目録、検証マニフェスト |
| Binance・Coinbase・Kraken原データ | 非収録 | 再取得スクリプトと取得記録のみ収録 |
| Cosmos Hub完全RPC応答 | 非収録 | 再取得・検証スクリプトと処理済みマニフェストのみ収録 |
| Binance履歴板 | 非収録 | イベント時点の履歴order-book snapshotは取得できていない |

訂正版データ（v0.1.1、2026年9月15日）の中間データは385ファイル、67,614,461 bytesです。
ファイル一覧とSHA-256は
[metadata/file_inventory.csv](metadata/file_inventory.csv)、
集計値は[metadata/dataset_summary.json](metadata/dataset_summary.json)にあります。

### 対象期間と分析単位

すべての解析時刻はUTCです。

| 対象 | 区間又は時刻 |
|---|---|
| 観測された最安値 | 2025-10-10T21:20:37.689043Z |
| 詳細約定抽出 | [2025-10-10T21:15:00Z, 21:25:00Z) |
| 主イベント窓 | [2025-10-10T20:30:00Z, 22:30:00Z) |
| Cosmos Hub詳細区間 | 2025-10-09から2025-10-12まで |
| 30日平時対照 | [2025-09-10T00:00:00Z, 2025-10-10T00:00:00Z) |
| 主対照分布 | 非重複2時間窓360個 |
| 同時刻感度分析 | 20:30–22:30 UTCの30窓 |
| 全期間リード・ラグ標本 | 2025年9月10日から10月10日当日までの31日 |
| リード・ラグ | 1分系列±5分、5分系列±60分 |

イベント図の1分足は20:30–22:30 UTCの半開区間120本です。循環シフトの帰無統計も、
観測値と同じラグ端除外・有限ペアのPearson相関で計算し、欠測による時間の隙間を
詰めません。全期間は5,000シフト、局所4時間の5分系列は適格な全23シフトを用います。

### 主な中間ファイル

#### 市場イベント

- [10分間のATOM/USDT約定抽出](data/processed/event_window/binance_spot_atomusdt_trades_2025-10-10_2115-2125_utc.csv.gz)
  最安値前後の価格、数量、約定時刻、売買主導方向を収録します。

#### Cosmos Hub

- [ATOM移転イベント](data/processed/cosmoshub/atom_transfers_2025-10-09_2025-10-12.jsonl.gz)
  成功したuatom移転イベントを正規化した4日間のテーブルです。
- [IBC送受信イベント](data/processed/cosmoshub/ibc_transfers_2025-10-09_2025-10-12.jsonl.gz)
  ICS-20送受信、チャネル、方向、数量及び相手チェーン候補を収録します。
- [イベント窓フロー](data/processed/cosmoshub/event_window_flows_2025-10-10_2030-2230_utc.jsonl.gz)
  主イベント窓に限定したATOM移転・IBC統合テーブルです。
- [取引所流入候補](data/processed/cosmoshub/exchange_inflow_candidates_2025-10-09_2025-10-12.json)
  確認済みラベルとは分離した、行動ベース候補と大口監視候補のレジストリです。
- [30日5分オンチェーンパネル](data/processed/cosmoshub/baseline_30d/baseline_5min_2025-09-10_2025-10-10.jsonl.gz)
  30日間・8,640行の連続5分系列です。
- [30日ブロック時刻索引](data/processed/cosmoshub/baseline_30d/block_times_2025-09-10_2025-10-10.jsonl.gz)
  高さ、UTC時刻、ブロックハッシュ及びトランザクション数を収録します。
- [30日受信ポリシー監査](data/processed/cosmoshub/baseline_30d/ibc_receive_policy_audit.json)
- [受信証跡](data/processed/cosmoshub/baseline_30d/ibc_receive_evidence.jsonl.gz)と[compact取引索引](data/processed/cosmoshub/baseline_30d/ibc_raw_transaction_index.jsonl.gz)

中間データ全体の説明は
[data/processed/README.md](data/processed/README.md)、
列定義は[docs/data_dictionary.md](docs/data_dictionary.md)を参照してください。

IBC流入は`native-atom-receipt-success-v2`で定義し、リレー取引全体の成功だけでは
受信とみなしません。正確なnative ATOM返送trace、対応するアプリケーション成功イベント、
native creditを確認します。PFMの遅延ACKを伴う成功受領は維持し、流出は宛先決済完了
ではなくHubでの送信開始を表します。

平時対照30日分を再取得・再監査し、旧候補からアプリケーション失敗18件と非native
返送trace 2件の計20件を除外しました。適格なnative受信は57,303件です。
4日間の詳細データでは別途失敗受信10件を除外していますが、期間が重なるため両者を
単純合計しません。イベント窓の1,113受信は変わらず、遅延ACKの583件も維持しています。

### 主な分析用テーブル

| ファイル | 内容 |
|---|---|
| [publication_control_windows.csv](results/publication_control_windows.csv) | 360個の非重複2時間対照窓 |
| [publication_matched_clock_windows.csv](results/publication_matched_clock_windows.csv) | 同一UTC時刻の30対照窓 |
| [publication_window_tests.csv](results/publication_window_tests.csv) | 経験的パーセンタイルとプラス1補正経験的裾確率 |
| [publication_lead_lag.csv](results/publication_lead_lag.csv) | 26組のリード・ラグ要約 |
| [publication_lead_lag_curves.csv](results/publication_lead_lag_curves.csv) | 全510ラグ点の相関曲線 |
| [wallet_coordination_candidates.csv](results/wallet_coordination_candidates.csv) | イベント前取引所流入送信元の記述的候補表 |
| [flash_sell_sequence.csv](results/flash_sell_sequence.csv) | 最終安値と同一マイクロ秒の売り主導系列 |

resultsディレクトリは論文記載値の検証用出力であると同時に、追加統計解析や作図に
利用できる整形済みテーブルです。

### クイックスタート

Python 3.11以降を使用します。

~~~bash
git clone https://github.com/yanagihalab/atom-flash-crash-research.git
cd atom-flash-crash-research

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

make validate
make verify
~~~

`make validate`はGit追跡済み及び除外されていない未追跡ファイル、JSON構文、gzip/ZIP、
単一ファイルサイズ、ローカル絶対パス及び秘密情報らしい文字列を検査します。
`make verify`は保存済みの公表用検定表と、明示的なprocessed-onlyモードによる
中間データのSHA・ウォレット指標を検査します。原データは不要ですが、市場原ファイルの
SHA、92約定及び71集約約定の原データ照合を省略したことを別レポートに記録します。

個別に実行する場合は次のとおりです。

~~~bash
python scripts/validate_release.py --deep
python scripts/verify_cosmos_flows.py
python scripts/verify_cosmos_baseline_indexed.py
python scripts/verify_publication_extensions.py
python scripts/verify_wallet_coordination.py --processed-only
~~~

30日対照は完全なcompact bundleでオフライン検証できます。原市場データも用いた
検証は別の`make verify-raw`で実行し、walletのfull検証と独立した直接Pearson循環シフト
検証を含みます。原データ不足をprocessed-onlyへ自動的に切り替えてPASSとはしません。

### 中間データを使った分析

Git clone直後に、次の処理が可能です。

- data/processedからATOM移転、IBC及び確認済み取引所フローを再集計
- results/publication_*から対照窓検定、感度分析及びリード・ラグ結果を再検査
- 確認済み取引所アドレスだけの主分析と未確認候補を含む感度分析を比較
- アドレス集中度やイベントとの時間近接性を再評価
- 収録済みPDF図及び和文分析報告を論文作成へ利用

主要な処理手順は
[docs/reproducibility.md](docs/reproducibility.md)にまとめています。

### 原データからの完全再生成

原データはGit管理外のdata/rawへ取得します。第三者サービスの提供状況、
利用条件及び将来のAPI変更に注意してください。

~~~bash
python scripts/download_binance.py
python scripts/download_comparison_markets.py
python scripts/collect_cosmos_study_data.py
~~~

取得後、公開済みの検証済み30日bundleを用いて次の分析を再生成できます。

~~~bash
python scripts/build_event_extract.py
python scripts/extract_cosmos_flows.py
python scripts/compare_control_windows.py
python scripts/analyze_publication_extensions.py
python scripts/analyze_wallet_coordination.py
~~~

既定の取得コマンドだけでは拡張30日分の市場原データは揃いません。
[docs/reproducibility.md](docs/reproducibility.md)のbaseline設定を用いてください。
indexed collector単体は完全なv2監査・証跡・旧manifest bundleを作成しません。
旧対照を保持したraw受信再抽出には同ガイドの移行手順を用い、ポリシー名の追記だけで
対照を認証したり、既存データを上書きしたりしないでください。

RPC提供状況により取得元の変更が必要になることがあります。取得元と実行記録は
[metadata/source_registry.json](metadata/source_registry.json)及び
[metadata/runs/](metadata/runs/)に記録しています。

### 再現対象となる主要結果

窓比較の`p`は記述的なプラス1補正経験的裾確率です。系列依存や比較集合の重複により
厳密な交換可能性は仮定しません。循環シフトp値は別の明示的な帰無分布に基づきます。

- ATOM/USDTのイベント2時間窓における値幅、約定数及びATOM/USDCとの安値乖離は、
  直前30日の360対照窓すべてを上回りました。
- 公開情報との完全一致で確認した取引所アドレスへのイベント窓流入は
  93.9パーセンタイル、plus-one p=0.0637であり、記述的な裾確率は0.05を上回ります。
- IBC受信訂正後も、報告したIBCのイベント順位とplus-one p値はすべて不変です。
  流入は360窓に対して98.6パーセンタイル（p=0.0166）、同時刻30窓ではp=0.0645です。
- 全期間5分の主要な取引所流入／市場ストレス検定は、ラグ−60分でr=0.0274、
  訂正したmax-lag p=0.0602です。局所4時間の感度分析はp=0.2083であり、
  いずれも因果方向を確定する結果ではありません。
- 最終安値は、同一マイクロ秒の連続92約定、合計9,695.33 ATOMの売り主導系列で
  形成されました。
- 直前のオンチェーン入金との時間的近接は観測できますが、取引所内部の対応キーが
  ないため、売り系列と同一利用者又は注文へ結び付けられません。
- 直前送信元の10種類のメモclusterは、その送信元のBinance宛て59送金に限定した数であり、
  全送金先への出金を合算した数ではありません。
- 未確認候補を加えても、特定ウォレットによる操作という帰属結論は支持されません。

これらはデータセット内で再現される記述的・統計的結果であり、因果関係や不正行為を
認定するものではありません。

### 完全性と来歴

- Binance取得時には提供元の.CHECKSUMとSHA-256を照合
- Cosmos Hub取得時には連続ブロック高、UTC境界、ページング及びトランザクション数を確認
- 独立した複数RPC間で対象ブロックと結果ハッシュを照合
- 中間ファイルを[SHA-256目録](metadata/file_inventory.csv)で固定
- 公表用検定36行のCSV/JSON整合性を確認し、リード・ラグ要約26行を保存済み曲線510行の極値と照合
- 通常の検査では360対照窓から順位・裾確率を再計算。同時刻30対照については保存済みCSV/JSONの一致を確認し、対照分布や循環シフト帰無分布の独立再計算は行わない
- 別の原データ依存・直接Pearson検証では、主要な取引所流入／市場ストレス系列の全標本・局所4時間標本に限り、循環シフト帰無分布を独立再計算
- 履歴板が存在しないことと、許容される主張表現を機械検証

最終検証結果は
[publication_additional_verification.json](results/publication_additional_verification.json)及び
[wallet_coordination_verification.json](results/wallet_coordination_verification.json)にあります。
限定的な検証範囲は別の[processed-onlyレポート](results/wallet_coordination_processed_verification.json)に、
主要な全シフトの保持原データ照合は[直接Pearson検証](results/publication_circular_shift_verification.json)に記録しています。
[公開整形の来歴mapping](metadata/publication_packaging_manifest.json)には元source/report SHAと
公開artifactへの再束縛を保存し、rawや原実行コードの証跡を後日の公開整形SHAで置き換えません。

### ディレクトリ構成

~~~text
config/          取得対象、イベント窓及び平時対照の設定
data/processed/  Gitで共有する分析用中間データ
docs/            データ辞書、再現手順、倫理上の注意
figures/         論文用の再生成可能なPDF図
metadata/        データ源、取得記録、ラベル、SHA-256目録
outputs/         和文分析報告と分析ワークブック
results/         集計値、検定、リード・ラグ、検証結果
scripts/         取得、抽出、集計、分析、検証、作図コード
~~~

### 限界と倫理

- イベント直前のBinance ATOM/USDT履歴板がなく、板厚、取消し、キュー年齢及び
  真の流動性枯渇を再構成できません。
- 公開約定ファイルには利用者ID、口座ID、共通テイカー注文ID、APIキー情報及び
  清算フラグがありません。
- Cosmos Hub送金時刻は取引所内部の入金反映時刻ではありません。
- IBC方向はCosmos Hubから見たパケット方向であり、中央集権取引所の純流入を意味しません。
- 行動ベース候補は所有者ラベルではなく、感度分析専用です。
- アドレス又はメモを自然人の特定や不正行為の断定に使用してはいけません。

詳細は[docs/ETHICS.md](docs/ETHICS.md)を参照してください。

### ライセンスと引用

- コード：[MIT License](LICENSE)
- 著者作成の文書・図・中間データ・分析結果：[CC BY 4.0](LICENSE-DATA.md)
- 第三者原データ：本リポジトリには含めず、再ライセンスしません
- 引用情報：[CITATION.cff](CITATION.cff)

論文又は二次分析では、使用したリリース番号又はコミットハッシュも併記してください。
