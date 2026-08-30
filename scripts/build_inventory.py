#!/usr/bin/env python3
"""Create a stable SHA-256 inventory for Git-published intermediate files."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_group(relative: Path) -> str:
    return "analysis_intermediate" if relative.parts[:2] == ("data", "processed") else "other"


def main() -> None:
    roots = [PROJECT_ROOT / "data" / "processed"]
    files = sorted(
        path
        for root in roots
        if root.exists()
        for path in root.rglob("*")
        if path.is_file() and path.name != "README.md" and not path.name.endswith(".part")
    )
    rows = []
    totals: dict[str, dict[str, int]] = defaultdict(lambda: {"file_count": 0, "size_bytes": 0})
    for path in files:
        relative = path.relative_to(PROJECT_ROOT)
        group = source_group(relative)
        size = path.stat().st_size
        rows.append({"source_group": group, "relative_path": relative.as_posix(), "size_bytes": size, "sha256": sha256_file(path)})
        totals[group]["file_count"] += 1
        totals[group]["size_bytes"] += size

    metadata = PROJECT_ROOT / "metadata"
    metadata.mkdir(parents=True, exist_ok=True)
    inventory = metadata / "file_inventory.csv"
    temporary = inventory.with_name(inventory.name + ".part")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["source_group", "relative_path", "size_bytes", "sha256"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, inventory)

    summary = {
        "study_id": "atom_flash_crash_2025_10_10",
        "inventory_path": str(inventory.relative_to(PROJECT_ROOT)),
        "file_count": len(rows),
        "size_bytes": sum(row["size_bytes"] for row in rows),
        "groups": dict(sorted(totals.items())),
    }
    (metadata / "dataset_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
