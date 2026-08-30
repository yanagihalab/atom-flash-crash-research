#!/usr/bin/env python3
"""Validate the public Git working tree before a GitHub push."""

from __future__ import annotations

import argparse
import gzip
import json
import re
import subprocess
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_GIT_FILE_BYTES = 100 * 1024 * 1024
TEXT_SUFFIXES = {".cff", ".csv", ".json", ".jsonl", ".md", ".py", ".txt", ".yml", ".yaml"}
REQUIRED = [
    "README.md",
    "CITATION.cff",
    "LICENSE",
    "LICENSE-DATA.md",
    "DATA_AVAILABILITY.md",
    "docs/data_dictionary.md",
    "docs/reproducibility.md",
    "docs/ETHICS.md",
    "metadata/source_registry.json",
    "metadata/exchange_address_labels.json",
    "metadata/file_inventory.csv",
    "metadata/dataset_summary.json",
    "results/publication_additional_verification.json",
    "results/wallet_coordination_verification.json",
]
SECRET_PATTERNS = {
    "GitHub token": re.compile(r"(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}"),
    "OpenAI key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private key": re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
}
ABSOLUTE_LOCAL_PATH = re.compile(
    r"(?:/" + r"Users/[^/]+/|/" + r"home/[^/]+/|[A-Za-z]:\\\\" + r"Users\\\\[^\\]+\\)"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deep", action="store_true", help="Read every ZIP and gzip stream")
    return parser.parse_args()


def iter_public_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )
    relatives = [Path(value.decode("utf-8")) for value in completed.stdout.split(b"\0") if value]
    return sorted(PROJECT_ROOT / relative for relative in relatives if (PROJECT_ROOT / relative).is_file())


def validate_text(path: Path, errors: list[str]) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        errors.append(f"non-UTF-8 text file: {path.relative_to(PROJECT_ROOT)} ({exc})")
        return
    if ABSOLUTE_LOCAL_PATH.search(text):
        errors.append(f"absolute local path: {path.relative_to(PROJECT_ROOT)}")
    for name, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            errors.append(f"possible {name}: {path.relative_to(PROJECT_ROOT)}")


def validate_archives(path: Path, deep: bool, errors: list[str]) -> None:
    if path.suffix == ".zip":
        try:
            with zipfile.ZipFile(path) as archive:
                if deep and archive.testzip() is not None:
                    errors.append(f"bad ZIP member: {path.relative_to(PROJECT_ROOT)}")
        except zipfile.BadZipFile as exc:
            errors.append(f"bad ZIP: {path.relative_to(PROJECT_ROOT)} ({exc})")
    elif path.suffix == ".gz" and deep:
        try:
            with gzip.open(path, "rb") as stream:
                while stream.read(1024 * 1024):
                    pass
        except (OSError, EOFError) as exc:
            errors.append(f"bad gzip: {path.relative_to(PROJECT_ROOT)} ({exc})")


def validate_json(path: Path, errors: list[str]) -> None:
    if path.suffix != ".json":
        return
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"bad JSON: {path.relative_to(PROJECT_ROOT)} ({exc})")


def main() -> None:
    args = parse_args()
    errors: list[str] = []
    for relative in REQUIRED:
        if not (PROJECT_ROOT / relative).is_file():
            errors.append(f"missing required file: {relative}")

    files = iter_public_files()
    total = 0
    largest = (0, "")
    for path in files:
        relative = path.relative_to(PROJECT_ROOT)
        size = path.stat().st_size
        total += size
        if size > largest[0]:
            largest = (size, relative.as_posix())
        if size >= MAX_GIT_FILE_BYTES:
            errors.append(f"Git file is at least 100 MiB: {relative} ({size} bytes)")
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in {"LICENSE", "Makefile"}:
            validate_text(path, errors)
        validate_json(path, errors)
        validate_archives(path, args.deep, errors)

    tracked_raw = [path for path in files if path.relative_to(PROJECT_ROOT).parts[:2] == ("data", "raw")]
    if tracked_raw:
        errors.append(f"raw source data must not be tracked in Git ({len(tracked_raw)} file(s))")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit(f"Release validation failed with {len(errors)} error(s)")

    print("PASS: public release validation")
    print(f"tracked Git files: {len(files)} files, {total} bytes")
    print(f"largest Git file: {largest[1]} ({largest[0]} bytes)")
    print(f"deep archive validation: {'enabled' if args.deep else 'disabled'}")


if __name__ == "__main__":
    main()
