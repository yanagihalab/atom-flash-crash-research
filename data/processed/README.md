# Analysis-ready intermediate data

This directory is the data payload tracked in Git. It contains normalized or
aggregated files used by the statistical analysis; third-party raw archives and
complete RPC responses are intentionally excluded.

## Contents

- `event_window/`: trade-level Binance ATOM/USDT extract for the ten-minute
  interval surrounding the observed low.
- `cosmoshub/atom_transfers_*.jsonl.gz`: normalized successful `uatom` transfer
  events for the four-day study interval.
- `cosmoshub/ibc_transfers_*.jsonl.gz`: decoded ICS-20 send/receive events.
- `cosmoshub/event_window_flows_*.jsonl.gz`: exact event-window subset used for
  the focused flow analysis.
- `cosmoshub/exchange_inflow_candidates_*.json`: confirmed-label separation and
  unconfirmed behavioral-candidate registry.
- `cosmoshub/baseline_30d/baseline_5min_*.jsonl.gz`: continuous five-minute
  on-chain analysis panel for the 30-day control interval.
- `cosmoshub/baseline_30d/block_times_*.jsonl.gz`: compact height/time index.
- `cosmoshub/baseline_30d/query_checkpoints/`: resumable aggregate checkpoints;
  these contain query hashes and partial bucket totals, not full RPC responses.

File sizes and SHA-256 digests are listed in `metadata/file_inventory.csv`.
Fields and interpretation rules are documented in `docs/data_dictionary.md`.
