# Changelog

## 0.1.1 - 2026-09-15

This correction synchronizes the complete verified 30-day receipt re-audit,
refreshed intermediate data, statistics, reports, workbook, provenance bindings,
and inventories. Cite the commit hash as well as the dataset version to identify
the exact files used; the version label does not imply a separate release asset.

- Require application-level receive success, an exact native-ATOM return denom,
  and a matching native credit; preserve valid PFM receipts with deferred ACKs.
- Distinguish Hub outbound send initiation from remote receipt or settlement.
- Exclude 20 legacy baseline receipt candidates (18 application failures and two
  non-native return traces), retaining 57,303 eligible native receipts. Event IBC
  percentiles and plus-one empirical tail probabilities remain unchanged after the correction.
- Match circular-shift nulls to the observed finite-pair, lag-trimmed Pearson
  statistic and preserve missing observations' time positions.
- Correct the primary full-sample five-minute max-lag p-value to 0.0602 and the
  local four-hour sensitivity p-value to 0.2083. Limit the nearest sender's 10
  distinct memo clusters explicitly to its 59 Binance-bound transfers.
- Add an independent raw-dependent direct-Pearson verifier and tests for receipt
  eligibility, baseline policy, circular shifts, and recoverable promotion.
- Align event figures to the half-open 120-minute window and assign every
  sell-sweep price to its own executed-quantity interval.
- Preserve the public repository's relative-path conventions and keep raw data,
  local backups, and manuscript sources/PDFs outside the release tree.
- Separate processed-only wallet checks from full raw verification, preserving
  the full report and explicitly recording skipped market checks; scan both
  tracked and non-ignored untracked release files without altering the Git index.
- Run offline unit tests, compact-baseline verification, and processed-only
  checks in GitHub Actions without requiring excluded raw archives.
- Align the wallet narrative with the manuscript's block-header timing,
  one-address scope, ownership limits, counterparty definitions, and descriptive
  post-selection statistics; retain the underlying numerical tables.
- Record source/report hashes and explicit public-byte artifact rebinding in a
  packaging provenance map. Preserve raw/code provenance and the exact prior
  public manifest; remove 31 unlisted legacy checkpoints from the release tree
  with recoverable local backups. The final processed inventory contains
  385 files totaling 67,614,461 bytes.

日本語：受信成功判定、同一定義のPearson循環シフト、図の半開区間・数量対応を修正しました。
30日対照を全件再監査し、失敗18件・非native 2件を除外して中間データ・統計値・Excel・
来歴・目録を同期しました。IBC順位とプラス1補正経験的裾確率は不変です。主要max-lag p値は0.0602、
局所感度分析は0.2083、10種類のメモはBinance宛て59送金に限定します。
公開対象は385中間ファイル・67,614,461 bytesで、旧checkpoint 31本は復旧可能な形で退避しています。
GitHub Actionsも公開中間データだけで検証できる構成に変更しました。
引用時はデータ版とcommitハッシュを併記し、使用したファイルを特定してください。

## 0.1.0 - 2026-08-30

- Initial analysis-intermediate-data release candidate.
- Includes the event extract, processed Cosmos Hub data, analysis tables,
  validation scripts, data dictionary, provenance metadata, and figure builders.
- Excludes third-party raw market files and complete Cosmos Hub RPC responses
  from Git; acquisition scripts and run records document their provenance.
