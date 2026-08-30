# Data dictionary

All study timestamps are UTC. The event timestamp used for alignment is
`2025-10-10T21:20:37.689043Z` (`2025-10-11T06:20:37.689043+09:00`).

## Binance spot trades

Raw source: Binance Public Data daily `trades` archives.

| Field | Type | Meaning |
|---|---|---|
| `trade_id` | integer | Exchange-assigned trade identifier |
| `price` | decimal string | Executed quote price |
| `quantity` | decimal string | Executed base-asset quantity |
| `quote_quantity` | decimal string | Executed quote-asset quantity |
| `time` | integer | Matching timestamp; microseconds for 2025 spot data |
| `is_buyer_maker` | boolean | True when the resting order was the buy order; aggressor was the seller |
| `is_best_match` | boolean | Binance best-match indicator |

The local immutable ZIP and its provider-supplied `.CHECKSUM` are verified during
processing. Raw ZIP files are not tracked in Git and are never rewritten by the
processing pipeline.

## Binance klines

The columns are open time, open, high, low, close, base volume, close time,
quote volume, trade count, taker-buy base volume, taker-buy quote volume, and an
unused field. `1s` files support event reconstruction; `1m` files support
cross-asset controls.

## Binance USD-M futures

`trades` contains executed contract trades. `klines` contains contract OHLCV.
`markPriceKlines`, `indexPriceKlines`, and `premiumIndexKlines` must not be
treated as interchangeable: the mark price is the liquidation-oriented reference,
while the contract trade price is an actual execution price.

## Kraken PostTrade

Each gzip JSONL row preserves one trade object returned by Kraken. Relevant
fields are `trade_id`, `price`, `quantity`, `symbol`, `trade_ts`, and
`publication_ts`. Daily partitions use half-open UTC intervals `[day, next day)`.

## Coinbase Exchange candles

Each row has `[time, low, high, open, close, volume]`. `time` is the UTC bucket
start in epoch seconds and `volume` is base-asset volume. Coinbase omits intervals
with no ticks; missing intervals are recorded in the run manifest and must not be
interpreted as zero prices or zero-volume observations without an explicit rule.

## Processed event extract

`data/processed/event_window/binance_spot_atomusdt_trades_2025-10-10_2115-2125_utc.csv.gz`
contains the half-open interval `[21:15:00, 21:25:00)` UTC. The derived
`aggressor_side` is `sell` when `is_buyer_maker=true`, otherwise `buy`.

## Cosmos Hub archive RPC source data

Raw source: three independent public `cosmoshub-4` archive RPC endpoints. The
locally acquired daily range is `2025-10-09` through `2025-10-12` UTC, covering block
heights 27,880,743 through 27,939,154 inclusive.

Each daily `block_metas.jsonl.gz` row contains the source RPC, the range used in
the `blockchain` request, and one CometBFT `block_meta` object. Important fields
are `block_id.hash`, `header.height`, `header.time`, `header.chain_id`,
`header.data_hash`, `header.app_hash`, and `num_txs`.

Each daily `tx_search.jsonl.gz` row contains `source_rpc`, `height`, `page`, and
the unmodified JSON-RPC response to an exact-height `tx_search` query. Every UTC
height has at least page 1, including zero-transaction blocks. When a block has
more than 100 transactions, subsequent pages are retained. Each transaction
contains its hash, height, index, base64-encoded raw Tx bytes, and `tx_result`
with execution code, gas, log, and events.

The half-open event interval `[2025-10-10T20:30:00Z,
2025-10-10T22:30:00Z)` is also retained locally as complete `block` and `block_results`
JSON-RPC responses. It covers heights 27,907,815 through 27,909,032 inclusive.
This preserves block commits and consensus-level finalize-block events that are
not part of the compact four-day files.

`results/cosmoshub_data_verification.json` records the independent verification:
gzip/JSONL readability, continuous heights, UTC boundaries, pagination, artifact
SHA-256 values, Tx raw-byte hashes, block transaction counts, and event-window
agreement among `block`, `block_results`, and `tx_search`. The three RPCs also
agree on block 27,908,328 and its block-results content hash.

These raw records are not tracked in Git. The derived records below support
analysis of transfers, IBC activity, and timing. They do
not identify the owner of an address or prove that an on-chain transfer caused a
specific exchange trade, order, or liquidation.

## Derived Cosmos Hub flows

`data/processed/cosmoshub/atom_transfers_2025-10-09_2025-10-12.jsonl.gz`
contains one row per successful `uatom` transfer event. Core fields are
`time_utc`, `height`, `tx_hash`, `tx_index`, `msg_index`, `message_type`,
`flow_class`, `sender`, `recipient`, `amount_uatom`, `amount_atom`, `tx_memo`,
the two memo-pattern flags, `is_event_window`, and `seconds_from_flash`.
`flow_class=direct_bank` is used for address-to-address exchange-flow analysis;
fees, staking, distributions, and IBC escrow are kept in other classes.

`data/processed/cosmoshub/ibc_transfers_2025-10-09_2025-10-12.jsonl.gz`
contains decoded ICS-20 packet sends and receives. It records `direction`, both
channel endpoints, packet sequence and timeout, sender, receiver, denom, amount,
`is_atom`, `amount_atom`, and a counterparty-chain hint inferred from address
prefixes. IBC inbound/outbound describes Cosmos Hub packet direction and is not
centralized-exchange net flow.

`data/processed/cosmoshub/event_window_flows_2025-10-10_2030-2230_utc.jsonl.gz`
is an exact subset of the two four-day flow tables with an added `flow_type`.
The independent multiset and checksum checks are recorded in
`results/cosmoshub_flow_verification.json`.

## Exchange-inflow candidates and labels

`data/processed/cosmoshub/exchange_inflow_candidates_2025-10-09_2025-10-12.json`
separates structured behavioral candidates from a large-flow watchlist.
Structured evidence means repeat/multi-sender receiving activity combined with
a numeric 4–20 character memo or a 16–32 character hexadecimal routing-style
memo. This is consistent with shared deposit routing but is not proof of
exchange ownership. Large receivers without enough structured-memo evidence are
kept in `large_flow_watchlist` and are not counted as exchange-like candidates.

`metadata/exchange_address_labels.json` is a separate exact-address registry
supported by retained public-source URLs and an explicit confidence level.
Labels are not inferred from transfer behavior. Public-record association still
does not identify the beneficial owner of a deposit or establish that deposited
ATOM was sold during the event.

## Shifted control windows

`results/control_window_comparison.json` compares the event interval with four
focal two-hour windows. `matched_pre_day` is the primary control at
`2025-10-09T20:30:00Z–22:30:00Z`; `same_day_pre` is the immediately adjacent
pre-event sensitivity interval; and `matched_post_day1` and
`matched_post_day2` are recovery-period sensitivity intervals at the same UTC
clock time. Post-event windows must not be described as uncontaminated normal
periods.

The `non_event_distribution` section contains 36 non-overlapping two-hour bins
from 2025-10-09, 2025-10-11, and 2025-10-12. It reports event-to-control
medians and empirical percentiles for direct ATOM movement, structured
candidates, public-label exchange flows, IBC, and Binance ATOM/USDT activity.
The bins provide a descriptive reference distribution; overlapping economic
conditions and the short four-day acquisition range preclude treating them as
independent identically distributed samples.

Market control rows are computed from Binance one-minute spot/futures/mark
archives, Kraken trade data, and Coinbase one-minute candles. Price range is
`(high-low)/open`; return is `close/open-1`; sell-aggressor share for Binance
klines is `(base volume-taker-buy base volume)/base volume`. Minute-level
discount fields compare the low of Binance ATOM/USDT with the low of the paired
ATOM/USDC or Coinbase ATOM/USD minute where both observations exist.

`results/control_window_verification.json` checks all source SHA-256 values,
window duration and membership, event reconciliation with the primary Cosmos
flow analysis, and workbook-driving distribution medians and percentiles.

## Thirty-day publication baseline

`data/processed/cosmoshub/baseline_30d/block_times_2025-09-10_2025-10-10.jsonl.gz`
contains one compact record per Cosmos Hub block in the half-open pre-event
window. Fields are `height`, `time_utc`, `block_hash`, `num_txs`, and
`source_rpc`.

`data/processed/cosmoshub/baseline_30d/baseline_5min_2025-09-10_2025-10-10.jsonl.gz`
contains 8,640 continuous five-minute rows. Each row includes event counts and
ATOM amounts for confirmed public-label exchange inflow/outflow, unconfirmed
behavioral-candidate inflow, and IBC
inbound/outbound. Derived fields are confirmed exchange net inflow, confirmed
plus unconfirmed behavioral-candidate inflow, and IBC net inbound flow.

Large-flow watchlist addresses are intentionally not queried for the 30-day
baseline because they are not exchange-flow candidates. Their event-centered
four-day records remain retained separately in the candidate registry and full
flow tables.

`baseline_indexed_manifest.json` records the validated RPC endpoints, height
boundaries, SHA-256 values, query counts, page counts, page-source provenance,
query hashes, extracted-event counts/amounts/digests, and decoding errors. Full
30-day `tx_search` response bodies are not duplicated. The exact 2025-10-09 UTC
overlap is independently reconciled against the retained full-transaction data
in `results/cosmos_baseline_30d_verification.json`.

## Publication extension outputs

`results/publication_window_tests.csv` has one row per metric and control set.
`all_360` is the main distribution of 360 non-overlapping two-hour windows;
`matched_clock_30` is the same-clock sensitivity distribution. Core fields are
the event value, control mean/median/empirical 5th and 95th percentiles, event
empirical percentile, prespecified tail direction, exceedance count, and the
plus-one randomization p-value.

`results/publication_lead_lag.csv` reports the maximum absolute correlation and
its lag, the zero-lag correlation, paired observation count, and a circular-shift
max-lag p-value. `results/publication_lead_lag_curves.csv` retains every tested
lag. Positive lag means x leads y. One-minute lags cover ±5 minutes; five-minute
lags cover ±60 minutes. Nonnegative flow/activity variables use `log1p`; signed
net flows use a median-scaled `asinh`; full-sample series are de-seasoned by UTC
minute or five-minute slot median.

Public-label addresses are the primary exchange-flow definition. Behavioral
high/medium candidates are used only in sensitivity analysis. The large-flow
watchlist is excluded from both exchange-flow definitions. The acquired dataset
does not contain historical Binance ATOM/USDT order-book snapshots, so the data
cannot reconstruct resting depth, cancellations, queue age, or internal
liquidations. Conclusions may state that the pattern is consistent with a
pair-specific liquidity or market-microstructure disruption; they must not state
that the data prove liquidity depletion or exhaustion.
