# Binance ATOM/USDT市場における瞬間的価格乖離の分析用中間データ

Analysis-Ready Intermediate Data for a Transient Price Dislocation in the Binance ATOM/USDT Market

[日本語](#japanese) | [English](#english)

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

リリースv0.1.0の中間データは411ファイル、55,336,556 bytesです。
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
| リード・ラグ | 1分系列±5分、5分系列±60分 |

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

中間データ全体の説明は
[data/processed/README.md](data/processed/README.md)、
列定義は[docs/data_dictionary.md](docs/data_dictionary.md)を参照してください。

### 主な分析用テーブル

| ファイル | 内容 |
|---|---|
| [publication_control_windows.csv](results/publication_control_windows.csv) | 360個の非重複2時間対照窓 |
| [publication_matched_clock_windows.csv](results/publication_matched_clock_windows.csv) | 同一UTC時刻の30対照窓 |
| [publication_window_tests.csv](results/publication_window_tests.csv) | 経験的パーセンタイルとplus-one置換検定 |
| [publication_lead_lag.csv](results/publication_lead_lag.csv) | 26組のリード・ラグ要約 |
| [publication_lead_lag_curves.csv](results/publication_lead_lag_curves.csv) | 全510ラグ点の相関曲線 |
| [wallet_coordination_candidates.csv](results/wallet_coordination_candidates.csv) | イベント前取引所流入送信元の記述的候補表 |
| [flash_sell_sequence.csv](results/flash_sell_sequence.csv) | 最終安値と同一マイクロ秒の売り主導系列 |

resultsディレクトリは論文記載値の検証用出力であると同時に、追加統計解析や作図に
利用できる整形済みテーブルです。

### クイックスタート

Python 3.11以降を使用します。

~~~bash
git clone <repository-url>
cd atom-flash-crash-research

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

make validate
make verify
~~~

make validateはGit追跡ファイル、JSON構文、gzip/ZIPストリーム、単一ファイルサイズ、
ローカル絶対パス及び秘密情報らしい文字列を検査します。make verifyは公表用検定表と
ウォレット帰属分析の整合性を再計算します。これらの検証に原データは不要です。

個別に実行する場合は次のとおりです。

~~~bash
python scripts/validate_release.py --deep
python scripts/verify_cosmos_flows.py
python scripts/verify_control_windows.py
python scripts/verify_publication_extensions.py
python scripts/verify_wallet_coordination.py
~~~

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
python scripts/collect_cosmos_baseline_indexed.py --start 2025-09-10T00:00:00Z --end-exclusive 2025-10-10T00:00:00Z
~~~

取得後、次の分析を再生成できます。

~~~bash
python scripts/build_event_extract.py
python scripts/extract_cosmos_flows.py
python scripts/compare_control_windows.py
python scripts/analyze_publication_extensions.py
python scripts/analyze_wallet_coordination.py
~~~

RPC提供状況により取得元の変更が必要になることがあります。取得元と実行記録は
[metadata/source_registry.json](metadata/source_registry.json)及び
[metadata/runs/](metadata/runs/)に記録しています。

### 再現対象となる主要結果

- ATOM/USDTのイベント2時間窓における値幅、約定数及びATOM/USDCとの安値乖離は、
  直前30日の360対照窓すべてを上回りました。
- 公開情報との完全一致で確認した取引所アドレスへのイベント窓流入は
  93.9パーセンタイル、plus-one p=0.0637であり、5%水準の上側異常ではありません。
- 最終安値は、同一マイクロ秒の連続92約定、合計9,695.33 ATOMの売り主導系列で
  形成されました。
- 直前のオンチェーン入金との時間的近接は観測できますが、取引所内部の対応キーが
  ないため、売り系列と同一利用者又は注文へ結び付けられません。
- 未確認候補を加えても、特定ウォレットによる操作という帰属結論は支持されません。

これらはデータセット内で再現される記述的・統計的結果であり、因果関係や不正行為を
認定するものではありません。

### 完全性と来歴

- Binance取得時には提供元の.CHECKSUMとSHA-256を照合
- Cosmos Hub取得時には連続ブロック高、UTC境界、ページング及びトランザクション数を確認
- 独立した複数RPC間で対象ブロックと結果ハッシュを照合
- 中間ファイルを[SHA-256目録](metadata/file_inventory.csv)で固定
- 公表用検定36行、リード・ラグ要約26行、曲線510行を独立検証
- 履歴板が存在しないことと、許容される主張表現を機械検証

最終検証結果は
[publication_additional_verification.json](results/publication_additional_verification.json)及び
[wallet_coordination_verification.json](results/wallet_coordination_verification.json)にあります。

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

Release v0.1.0 contains 411 intermediate data files totaling 55,336,556 bytes.
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
| Lead-lag ranges | ±5 minutes at 1-minute resolution; ±60 minutes at 5-minute resolution |

### Main intermediate artifacts

- [Ten-minute ATOM/USDT trade extract](data/processed/event_window/binance_spot_atomusdt_trades_2025-10-10_2115-2125_utc.csv.gz)
- [Normalized ATOM transfers](data/processed/cosmoshub/atom_transfers_2025-10-09_2025-10-12.jsonl.gz)
- [Decoded IBC sends and receives](data/processed/cosmoshub/ibc_transfers_2025-10-09_2025-10-12.jsonl.gz)
- [Combined event-window flows](data/processed/cosmoshub/event_window_flows_2025-10-10_2030-2230_utc.jsonl.gz)
- [Exchange-inflow candidate registry](data/processed/cosmoshub/exchange_inflow_candidates_2025-10-09_2025-10-12.json)
- [Continuous 30-day five-minute on-chain panel](data/processed/cosmoshub/baseline_30d/baseline_5min_2025-09-10_2025-10-10.jsonl.gz)
- [Compact 30-day block-time index](data/processed/cosmoshub/baseline_30d/block_times_2025-09-10_2025-10-10.jsonl.gz)

See [data/processed/README.md](data/processed/README.md) for the intermediate-data
contract and [docs/data_dictionary.md](docs/data_dictionary.md) for field definitions.

### Main analysis tables

| File | Contents |
|---|---|
| [publication_control_windows.csv](results/publication_control_windows.csv) | 360 non-overlapping two-hour controls |
| [publication_matched_clock_windows.csv](results/publication_matched_clock_windows.csv) | 30 matched-clock controls |
| [publication_window_tests.csv](results/publication_window_tests.csv) | Empirical percentiles and plus-one permutation tests |
| [publication_lead_lag.csv](results/publication_lead_lag.csv) | 26 lead-lag summaries |
| [publication_lead_lag_curves.csv](results/publication_lead_lag_curves.csv) | All 510 evaluated lag points |
| [wallet_coordination_candidates.csv](results/wallet_coordination_candidates.csv) | Descriptive pre-event exchange-inflow sender candidates |
| [flash_sell_sequence.csv](results/flash_sell_sequence.csv) | Sell-aggressor sequence at the final-low microsecond |

The files in results are both manuscript-verification outputs and tidy inputs for
additional statistical analysis or visualization.

### Quick start

Python 3.11 or later is recommended.

~~~bash
git clone <repository-url>
cd atom-flash-crash-research

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

make validate
make verify
~~~

make validate checks tracked files, JSON syntax, gzip/ZIP streams, individual
file size, local absolute paths, and token-like secrets. make verify independently
checks the publication tables and wallet-attribution analysis. Neither command
requires the excluded raw source files.

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
python scripts/collect_cosmos_baseline_indexed.py --start 2025-09-10T00:00:00Z --end-exclusive 2025-10-10T00:00:00Z
~~~

After acquisition:

~~~bash
python scripts/build_event_extract.py
python scripts/extract_cosmos_flows.py
python scripts/compare_control_windows.py
python scripts/analyze_publication_extensions.py
python scripts/analyze_wallet_coordination.py
~~~

Archive RPC availability may change. Sources and recorded acquisition runs are
documented in [metadata/source_registry.json](metadata/source_registry.json)
and [metadata/runs/](metadata/runs/).

### Main reproducible findings

- The ATOM/USDT two-hour event-window price range, trade count, and low-price
  dislocation relative to ATOM/USDC exceed all 360 pre-event control windows.
- Inflow to exact-address, publicly confirmed exchange labels is at the 93.9th
  percentile with plus-one p=0.0637; it is not an upper-tail anomaly at the 5% level.
- The terminal low was formed by 92 consecutive sell-aggressor executions in the
  same microsecond, totaling 9,695.33 ATOM.
- A temporally nearby on-chain deposit is observable, but public data contain no
  exchange-internal join key connecting it to the sell sequence or a user.
- Adding unconfirmed candidates does not establish wallet-specific manipulation.

These are descriptive and statistical findings within the dataset. They do not
establish causality, intent, or wrongdoing.

### Integrity and provenance

- Binance archives were checked against provider .CHECKSUM SHA-256 values.
- Cosmos Hub acquisition was checked for continuous heights, UTC boundaries,
  pagination, and transaction counts.
- Selected blocks and result hashes were cross-checked across independent RPC endpoints.
- Intermediate files are fixed by the [SHA-256 inventory](metadata/file_inventory.csv).
- The 36 publication-test rows, 26 lead-lag summaries, and 510 curve rows are independently verified.
- The absence of historical order-book data and the permitted scope of claims are machine-checked.

Final checks are recorded in
[publication_additional_verification.json](results/publication_additional_verification.json)
and [wallet_coordination_verification.json](results/wallet_coordination_verification.json).

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
