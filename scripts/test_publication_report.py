#!/usr/bin/env python3
"""Read-only regression tests for the publication report's Markdown tables."""

from __future__ import annotations

import re
import unittest

from build_publication_report import lead_lag_table


def markdown_cells(line: str) -> list[str]:
    """Split table cells without treating escaped absolute-value bars as pipes."""
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def example_row(sample: str, resolution: str, x: str) -> dict:
    return {
        "sample": sample,
        "resolution": resolution,
        "x": x,
        "y": "stress",
        "max_abs_correlation_lag_minutes": -60,
        "max_abs_correlation": -0.571013,
        "lag_zero_correlation": 0.012345,
        "circular_shift_max_lag_p": 0.208333333,
        "paired_observations_at_best_lag": 8916,
    }


class LeadLagTableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            example_row("30d_plus_event_day", "1m", "full_one"),
            example_row("30d_plus_event_day", "5m", "full_five"),
            example_row("event_local_4h", "1m", "local_one"),
            example_row("event_local_4h", "5m", "local_five"),
        ]

    def test_absolute_value_bars_are_escaped_in_header(self) -> None:
        header = lead_lag_table([], "30d_plus_event_day", "5m")[0]
        self.assertEqual(markdown_cells(header)[1], r"最大\|r\|ラグ (分)")
        self.assertNotIn("最大|r|", header)

    def test_header_separator_and_data_each_have_six_columns(self) -> None:
        for row in self.rows:
            with self.subTest(sample=row["sample"], resolution=row["resolution"]):
                lines = lead_lag_table(self.rows, row["sample"], row["resolution"])
                for line in lines:
                    self.assertEqual(len(markdown_cells(line)), 6, line)

    def test_selects_both_sample_and_resolution(self) -> None:
        for row in self.rows:
            with self.subTest(sample=row["sample"], resolution=row["resolution"]):
                lines = lead_lag_table(self.rows, row["sample"], row["resolution"])
                self.assertEqual(len(lines), 3)
                self.assertEqual(markdown_cells(lines[2])[0], f"{row['x']} → stress")
        self.assertEqual(len(lead_lag_table(self.rows, "missing", "5m")), 2)

    def test_numeric_values_and_sign_are_preserved(self) -> None:
        lines = lead_lag_table(self.rows, "event_local_4h", "5m")
        self.assertEqual(
            markdown_cells(lines[2]),
            ["local_five → stress", "-60", "-0.571", "0.012", "0.2083", "8,916"],
        )


if __name__ == "__main__":
    unittest.main()
