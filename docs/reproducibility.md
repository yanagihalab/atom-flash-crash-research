# 再現手順

## 1. 環境

Python 3.11以降を使用します。
以下のコマンドはすべてリポジトリのルートディレクトリで実行してください。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## 2. 公開ツリーの検証

```bash
python scripts/validate_release.py --deep
```

`--deep`は収録したZIP・gzipのCRC又はストリーム完全性、JSON構文、100 MiB未満の
単一ファイルサイズ、絶対ローカルパス及び秘密情報らしい文字列の混入を検査します。
追跡済みファイルだけでなく、`.gitignore`等で除外されていない未追跡ファイルも対象です。
除外済みのローカル原データ・一時ファイルは走査しません。シンボリックリンクは公開対象として拒否します。

## 3. Git収録中間データの確認と分析

```bash
python scripts/analyze_cosmos_flows.py
python scripts/verify_cosmos_flows.py
python scripts/verify_cosmos_baseline_indexed.py
make verify
```

`data/processed/`がGitで共有する中間データです。`results/`の対照窓、経験的検定、
リード・ラグ曲線及びウォレット候補表は、そのまま統計解析や作図へ入力できます。
上記検証には原データは不要です。`make verify`は保存されたpublication表・JSON・CSVの
整合性と、`verify_wallet_coordination.py --processed-only`による公開中間データの
入力SHA256・ウォレット指標・主張の制約を検査します。循環シフトの帰無分布再計算、
raw市場ファイルのSHA256照合、92約定及び71集約約定の原データ照合は行いません。
除外した検査と検証範囲を`results/wallet_coordination_processed_verification.json`へ明記し、
原データも用いた既存の`wallet_coordination_verification.json`は上書きしません。
新しい抽出データと古い分析結果のSHA256不一致は、processed-onlyでも失敗します。
30日対照の単独検証は、公開する完全なv2 bundle（series、block index、checkpoint、
受信証跡、compact取引索引、監査、旧manifest）で実行できます。原RPCの再抽出や
原ファイルの取得時SHA検査を改めて行うものではありません。

市場原データからの独立照合や、市場集計・イベント抽出・Cosmos Hubフロー抽出を
最初から再生成する場合は、以下の手順で第三者の原データをローカルの`data/raw/`へ取得します。
`data/raw/`はGit管理外です。

English: `make verify` checks the published processed inputs and saved analysis
tables; it does not independently verify raw market provenance, the 92 raw fills,
the 71 aggregate-trade rows, or the circular-shift null distribution. The explicit
processed-only wallet report records these skipped checks, checks source hashes
and wallet metrics, and uses a separate output file. Full raw verification never
silently falls back when raw files are missing.

## 4. 公開市場原データのローカル再取得

```bash
python scripts/download_binance.py
python scripts/download_comparison_markets.py
```

30日平時対照は次の設定で取得します。

```bash
python scripts/download_binance.py \
  --config config/baseline_30d.json --start 2025-09-10 --end 2025-10-08
python scripts/download_comparison_markets.py \
  --config config/baseline_30d.json --start 2025-09-10 --end 2025-10-08
```

## 5. Cosmos Hub原データのローカル再取得

```bash
python scripts/collect_cosmos_study_data.py \
  --rpc https://rpc.cosmoshub-main.ccvalidators.com \
  --rpc https://rpc.cosmoshub-4-archive.citizenweb3.com \
  --rpc https://cosmos-rpc.publicnode.com
```

RPC提供状況は将来変化し得ます。スクリプトはchain ID、取得可能なブロック時刻範囲、
ページング及び応答整合性を確認してから保存します。

取得後、次の処理で中間ファイルと統計結果を再生成できます。

```bash
python scripts/build_event_extract.py
python scripts/extract_cosmos_flows.py
python scripts/compare_control_windows.py
python scripts/analyze_publication_extensions.py
python scripts/analyze_wallet_coordination.py
```

`analyze_publication_extensions.py`には、受信成功ポリシーを満たす30日対照が必要です。
古いIBC集計にポリシー名だけを追記して使わず、受信原データから検証・再構築してください。
通常の再現は公開済みの完全なv2 bundleを第3節の手順で検証して用います。
`collect_cosmos_baseline_indexed.py`の新規取得出力だけでは、旧manifest・監査・受信証跡・
compact取引索引が揃わず、完全bundleの検証要件を満たしません。
旧対照を保持している場合の再取得・再構築は第9節の移行手順を用います。

検証には4日分の公開送金テーブルとの10月9日重複区間照合も含まれます。
作業先は既存中間データを上書きしない名前にし、検証がPASSするまで主要解析の入力を
切り替えないでください。採用時は`--baseline-series`で検証済み系列を明示するか、
下記の移行専用手順を用います。

## 6. 図の再生成

```bash
python scripts/build_figures.py
python scripts/build_timeseries_visualization.py
```

図の完全再生成にはローカルの市場原データが必要です。静的図は`figures/`、対話的時系列は
`outputs/atom_flash_crash_2025_10_10/atom_flash_crash_timeseries.html`へ出力されます。

## 7. 時刻と検定

- 全時刻はUTCです。
- イベント時刻は`2025-10-10T21:20:37.689043Z`です。
- 主対照は`[2025-09-10T00:00:00Z, 2025-10-10T00:00:00Z)`の30日です。
- 360個の非重複2時間窓を主分布、30個の同時刻窓を感度分析に用います。
- リード・ラグ全期間は9月10日から10月10日当日までを含む31日です。
- イベントの1分足は`[20:30, 22:30)`の半開区間120本で、22:30始まりの足を含みません。
- 窓比較のプラス1補正経験的裾確率は、系列依存のため厳密な交換可能性を仮定せず、平時分布中の稀少性を記述する量として解釈します。循環シフト検定は別の明示的な帰無分布を用います。
- 正のラグは$x$が$y$へ先行する定義です。
- 循環シフトの各帰無統計も、観測統計と同じラグ端除外・有限ペアのPearson相関で計算します。
  欠測点を取り除いて時間軸を詰めることはしません。

## 8. 追加の独立検証（原データが必要）

通常の`make verify`は公開中間ファイルだけを対象とします。
以下は追加の原データ依存検証であり、通常の`make verify`には含まれません。

```bash
python scripts/verify_publication_circular_shift.py --self-test
python scripts/analyze_publication_extensions.py
python scripts/verify_publication_circular_shift.py
python scripts/verify_publication_extensions.py
```

必要な原データを再取得済みで、分析結果が最新の入力と一致していれば、次の一括検査も利用できます。

```bash
make verify-raw
```

これは`verify_control_windows.py`、walletのfull検証、循環シフト専用検証を実行し、
併せて原RPC不要の30日compact bundle検証も実行します。
walletのデフォルトモード（`--processed-only`なし）はraw市場データ必須です。
原データ不足をスキップしてPASSとはしません。

循環シフト専用検証には、9月10日--10月10日のATOM/USDT現物1分足原ZIP、検証済み
30日5分フロー、イベント当日のBank送金を含む4日分中間データ、公開ラベル定義が必要です。
専用検証は分析本体のFFT実装を使わず、直接Pearson計算で主解析の全5,000シフトと
局所4時間の全23シフトを再計算します。結果JSON・CSVとの不一致は終了コード1となり、
`results/publication_circular_shift_verification.json`に入力ハッシュと検証結果を保存します。
原データを必要としないロジック単体テストは個別に実行できます。

```bash
python -m unittest discover -s scripts -p test_ibc_receive_evidence.py
python -m unittest discover -s scripts -p test_ibc_baseline_integrity.py
python -m unittest discover -s scripts -p test_publication_circular_shift.py
python -m unittest discover -s scripts -p test_publication_input_policy.py
python -m unittest discover -s scripts -p test_promote_verified_ibc_baseline.py
python -m unittest discover -s scripts -p test_wallet_verification_modes.py
python -m unittest discover -s scripts -p test_validate_release.py
```

受信成功は`tx_result.code=0`だけで決めず、正確なnative返送denom、同一メッセージの
受信成功イベント、native creditを照合します。PFMでは中間受取先を導出し、即時ACKが
なくても成功受信とnative creditが揃えば採用します。流出はHub送信開始であり、
宛先決済完了・DEX売買・Binance内約定を意味しません。

English: `make verify` remains a processed-data-only check. The additional
`verify_publication_circular_shift.py` requires retained raw one-minute market
archives and independently checks all primary full-sample/local circular shifts
using the same finite-pair, lag-trimmed Pearson statistic. Missing time positions
are preserved. Raw receipt verification distinguishes successful native Hub
credits from transaction success, permits deferred PFM acknowledgements, and
does not interpret outbound send initiation as destination settlement.

## 9. 旧IBC対照からの移行専用ツール

以下は修正前のaggregate-only受信対照を保持している場合の監査・移行用です。
既に受信成功ポリシー版になった公開データに、旧版移行を繰り返す手順ではありません。

- `collect_ibc_reaudit_raw.py`：`--mode estimate`で規模確認、`fetch`で取得、`verify`で
  ページング・Txハッシュ・保持原データを検証します。`--output`を分離して指定できます。
- `audit_completed_ibc_days.py`：保持済みの日別原データと旧抽出イベントのダイジェストを照合します。
- `rebuild_baseline_ibc_receipts.py`：`--raw-root`、`--baseline-root`、新規`--output-root`を
  指定し、完了済みの取得manifest・日別manifest・取得時raw SHA256を照合して原RPCから
  受信だけを再抽出します。旧版を保持し、Bank送金・IBC送信の値と341本のcheckpoint
  gzipをバイト単位で維持します。取引の全日重複と日付・height境界も確認します。
- `verify_cosmos_baseline_indexed.py`：候補ディレクトリを`--baseline-root`で独立検証します。
  完全なcompact bundleを用い、full raw RPCを読みません。日別クエリ・索引・証跡・
  checkpoint・全5分集計と4日テーブルの重複区間を照合します。
- `promote_verified_ibc_baseline.py`：候補の独立PASS、入力SHA256、受信ポリシー、旧manifest
  一致を確認します。`--candidate`、`--verification`、未使用の`tmp/pdfs/`配下の`--backup`
  を指定し、まず`--apply`なしで検証してください。`--apply`を付けた場合だけ切り替え、
  原canonicalディレクトリをバックアップに保存します。切替後はcanonical検証を再実行します。

監査の`extraction_code`は原実行時のコードSHA256です。公開用相対パスへの適応等で
後から変わったスクリプトのSHAへ置き換えません。path-onlyの公開整形が必要な場合は、
原source/report SHAと公開バイトのSHAを別mappingで記録し、意味内容の一致を確認します。

`verify_ibc_receive_correction.py`は、4日分の修正前バックアップと原データを使う
今回の訂正差分監査用です。必要な修正前バックアップがない新規clone向けの必須手順ではありません。
原RPCデータ・ローカルバックアップはGit管理外とし、再生成した監査ファイルを共有する前には
ローカル絶対パスの除去と公開ツリーの検証を行ってください。
