"""Promote the verified receipt-v2 baseline, keeping the prior tree recoverable.

This is an explicit local migration, not a collector. It requires the independent
v2 verification and matching input hashes; it never overwrites a backup folder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from rebuild_baseline_ibc_receipts import records, require_local_artifact, resolve_reference
from collect_cosmos_baseline_indexed import write_deterministic_jsonl_gzip

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1 << 20), b""):
            h.update(part)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=ROOT / "data/processed/cosmoshub/baseline_30d_success_v2")
    parser.add_argument("--verification", type=Path, default=ROOT / "results/cosmos_baseline_30d_success_v2_verification.json")
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Without this flag, validate only")
    args = parser.parse_args()
    candidate, backup = args.candidate.resolve(), args.backup.resolve()
    canonical = ROOT / "data/processed/cosmoshub/baseline_30d"
    if candidate == canonical or candidate.parent != canonical.parent:
        raise SystemExit("Candidate must be a separate sibling of the canonical study baseline")
    if backup.exists() or not backup.is_relative_to(ROOT / "tmp/pdfs"):
        raise SystemExit("Use a new, nonexistent backup directory under this study's tmp/pdfs")
    report = json.loads(args.verification.read_text())
    manifest_path = candidate / "baseline_indexed_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    try:
        series = require_local_artifact(manifest["five_minute_series"], candidate, ROOT,
                                        "baseline_5min_2025-09-10_2025-10-10.jsonl.gz")
        artifacts = {key: require_local_artifact(manifest[key], candidate, ROOT, name) for key, name in {
            "ibc_receive_policy_audit": "ibc_receive_policy_audit.json",
            "prior_baseline_snapshot": "baseline_indexed_manifest_before_success_v2.json",
            "ibc_receive_evidence": "ibc_receive_evidence.jsonl.gz",
            "ibc_raw_transaction_index": "ibc_raw_transaction_index.jsonl.gz",
        }.items()}
    except (KeyError, ValueError, TypeError, OSError) as error:
        raise SystemExit(f"Incomplete or non-local verified candidate: {error}") from error
    if (report.get("verification_status") != "PASS"
            or report.get("verified_inputs", {}).get("manifest_sha256") != digest(manifest_path)
            or report["verified_inputs"].get("series_sha256") != digest(series)):
        raise SystemExit("Independent PASS verification does not identify these exact candidate inputs")
    if manifest.get("ibc_inbound_extraction_policy") != "native-atom-receipt-success-v2":
        raise SystemExit("Candidate is not a success-verified native ATOM baseline")
    audit_path = candidate / "ibc_receive_policy_audit.json"
    if digest(audit_path) != manifest["ibc_receive_policy_audit"]["sha256"]:
        raise SystemExit("Receipt audit changed after independent verification")
    audit = json.loads(audit_path.read_text())
    if audit.get("status") != "PASS" or audit.get("unresolved_native_exclusions") != []:
        raise SystemExit("Receipt audit contains unresolved exclusions")
    block_name = Path(manifest["block_times"].get("local_path", manifest["block_times"].get("relative_path"))).name
    if (digest(canonical / block_name) != manifest["block_times"]["sha256"]
            or report["verified_inputs"].get("block_times_sha256") != manifest["block_times"]["sha256"]):
        raise SystemExit("Shared block-time index changed after independent verification")
    snapshot_name = "baseline_indexed_manifest_before_success_v2.json"
    snapshot = candidate / snapshot_name
    if snapshot.read_bytes() != (canonical / "baseline_indexed_manifest.json").read_bytes():
        raise SystemExit("Canonical baseline changed since the candidate was built")
    if (audit.get("old_baseline_manifest_sha256") != digest(snapshot)
            or resolve_reference(audit["old_baseline_manifest"], candidate, ROOT) != snapshot):
        raise SystemExit("Receipt audit does not identify the exact local prior snapshot")
    if not audit.get("legacy_digests_all_reproduced") or not audit.get("non_inbound_bucket_metrics_unchanged"):
        raise SystemExit("Candidate does not preserve the original non-inbound inputs")
    print(json.dumps({"validated": True, "apply": args.apply, "candidate": str(candidate),
                      "canonical": str(canonical), "backup": str(backup)}, indent=2))
    if not args.apply:
        return
    staged = Path(tempfile.mkdtemp(prefix=".baseline_receipt_v2_", dir=canonical.parent))
    # Build a complete replacement before either visible tree is moved.
    shutil.copytree(candidate, staged, dirs_exist_ok=True)
    shutil.copy2(canonical / block_name, staged / block_name)
    shutil.copy2(canonical / "block_times_manifest.json", staged / "block_times_manifest.json")
    def relocate(value):
        if isinstance(value, dict):
            return {key: relocate(item) for key, item in value.items()}
        if isinstance(value, list):
            return [relocate(item) for item in value]
        if isinstance(value, str):
            return value.replace(str(candidate) + "/", str(canonical) + "/").replace(
                candidate.relative_to(ROOT).as_posix() + "/", canonical.relative_to(ROOT).as_posix() + "/")
        return value
    # Rewrite live audit/checkpoint references before rebinding their parent
    # hashes. The historical snapshot is deliberately never parsed or rewritten.
    promoted_audit = relocate(audit)
    (staged / audit_path.name).write_text(json.dumps(promoted_audit, ensure_ascii=False, indent=2) + "\n")
    for path in (staged / "query_checkpoints").glob("*.jsonl.gz"):
        original_rows = list(records(path))
        relocated_rows = relocate(original_rows)
        if relocated_rows != original_rows:
            write_deterministic_jsonl_gzip(path, relocated_rows)
    promoted = relocate(manifest)
    promoted["ibc_receive_policy_audit"]["sha256"] = digest(staged / audit_path.name)
    promoted["block_times"]["local_path"] = str(canonical / block_name)
    promoted["query_checkpoint_directory"] = str(canonical / "query_checkpoints")
    (staged / "baseline_indexed_manifest.json").write_text(json.dumps(promoted, ensure_ascii=False, indent=2) + "\n")
    if digest(staged / series.name) != manifest["five_minute_series"]["sha256"]:
        raise SystemExit("Staged series changed; canonical tree is untouched")
    if (digest(staged / block_name) != manifest["block_times"]["sha256"]
            or digest(staged / audit_path.name) != promoted["ibc_receive_policy_audit"]["sha256"]
            or (staged / snapshot_name).read_bytes() != (canonical / "baseline_indexed_manifest.json").read_bytes()):
        raise SystemExit("Staged evidence or canonical manifest changed; canonical tree is untouched")
    if any(digest(staged / artifacts[key].name) != manifest[key]["sha256"]
           for key in ("ibc_receive_evidence", "ibc_raw_transaction_index", "prior_baseline_snapshot")):
        raise SystemExit("Staged evidence/index/snapshot changed; canonical tree is untouched")
    backup.parent.mkdir(parents=True, exist_ok=True)
    canonical.rename(backup)
    try:
        staged.rename(canonical)
    except BaseException:
        backup.rename(canonical)
        raise
    print("Promoted. Prior baseline is recoverable in the backup directory. Re-run canonical verification before analysis.")


if __name__ == "__main__":
    main()
