# 再現手順

## 1. 環境

Python 3.11以降を使用します。

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

## 3. Git収録中間データの確認と分析

```bash
python scripts/analyze_cosmos_flows.py
python scripts/verify_cosmos_flows.py
python scripts/verify_control_windows.py
python scripts/verify_publication_extensions.py
python scripts/verify_wallet_coordination.py
```

`data/processed/`がGitで共有する中間データです。`results/`の対照窓、経験的検定、
リード・ラグ曲線及びウォレット候補表は、そのまま統計解析や作図へ入力できます。
上記検証には原データは不要です。

市場集計、イベント抽出、Cosmos Hubフロー抽出を最初から再生成する場合のみ、以下の
手順で第三者の原データをローカルの`data/raw/`へ取得します。`data/raw/`はGit管理外です。

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
- plus-one置換`p`値は直列依存を考慮した記述的順位として解釈します。
- 正のラグは$x$が$y$へ先行する定義です。
