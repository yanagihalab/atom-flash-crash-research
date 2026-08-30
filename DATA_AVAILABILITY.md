# Data availability statement

The Git repository contains analysis-ready intermediate files: the event trade
extract, processed Cosmos Hub transfers and IBC records, the compact 30-day
block-time and five-minute baseline, analysis tables, source metadata, and
verification scripts.

Third-party raw files from Binance, Coinbase, Kraken, and Cosmos Hub RPC are not
tracked in Git and are not distributed as release assets. Acquisition scripts,
source URLs, run records, and derived-data checksums are retained so that the
intermediate files have an auditable provenance trail. Rebuilding every
intermediate file from source requires reacquiring the raw data; availability of
third-party endpoints and historical archives may change.

No historical Binance ATOM/USDT order-book snapshots were available to this study.
Consequently, the repository cannot reproduce resting depth, order cancellation,
queue age, internal exchange accounts, or liquidations.
