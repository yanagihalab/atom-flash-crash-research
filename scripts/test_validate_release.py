#!/usr/bin/env python3
"""Release scan tests; temporary repositories only, with no index writes."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import validate_release as validator


class ReleaseScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True, capture_output=True)
        (self.root / ".gitignore").write_text("data/raw/\n.venv/\ntmp/\n__pycache__/\n")
        self.root_patch = patch.object(validator, "PROJECT_ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def write(self, relative: str, value: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return path

    def validate(self) -> None:
        with patch.object(validator, "REQUIRED", []), patch.object(validator, "parse_args", return_value=argparse.Namespace(deep=True)), contextlib.redirect_stdout(io.StringIO()):
            validator.main()

    def test_scans_new_untracked_files_but_ignores_local_raw_and_cache(self) -> None:
        new = self.write("results/new_verification.json", json.dumps({"status": "PASS"}))
        self.write("data/raw/private.json", "{not JSON}")
        self.write(".venv/private.json", "{not JSON}")
        self.write("tmp/local.json", "{not JSON}")
        self.write("__pycache__/local.json", "{not JSON}")
        public = validator.iter_public_files()
        self.assertIn(new, public)
        self.assertEqual({path.relative_to(self.root).as_posix() for path in public},
                         {".gitignore", "results/new_verification.json"})
        self.validate()

    def test_untracked_bad_json_fails(self) -> None:
        self.write("metadata/new_manifest.json", "{not JSON}")
        with self.assertRaises(SystemExit):
            self.validate()

    def test_untracked_private_path_fails(self) -> None:
        self.write("results/new.json", json.dumps({"path": "/" + "Users/researcher/private/input"}))
        with self.assertRaises(SystemExit):
            self.validate()

    def test_untracked_secret_fails(self) -> None:
        self.write("scripts/new.py", "token = " + repr("ghp_" + "x" * 25))
        with self.assertRaises(SystemExit):
            self.validate()

    def test_tracked_ignored_raw_is_not_hidden_by_exclusion(self) -> None:
        raw = self.write("data/raw/tracked.json", "{}")
        completed = subprocess.CompletedProcess([], 0, stdout=b"data/raw/tracked.json\0data/raw/tracked.json\0")
        with patch.object(validator.subprocess, "run", return_value=completed) as invocation:
            self.assertEqual(validator.iter_public_files(), [raw])
            args = invocation.call_args.args[0]
            self.assertIn("--cached", args)
            self.assertIn("--others", args)
            self.assertIn("--exclude-standard", args)
            with self.assertRaises(SystemExit):
                self.validate()

    def test_symlink_is_rejected_without_reading_target(self) -> None:
        (self.root / "external.json").symlink_to(self.root / "missing-outside-target")
        self.assertIn(self.root / "external.json", validator.iter_public_files())
        with patch.object(validator, "validate_text") as inspect_text, self.assertRaises(SystemExit):
            self.validate()
        inspect_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
