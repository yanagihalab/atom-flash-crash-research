#!/usr/bin/env python3
"""Read-only tests for the same-statistic circular-shift lead-lag null."""

from __future__ import annotations

import math
import unittest

import numpy as np

from analyze_publication_extensions import (
    RNG_SEED,
    _circular_shift_offsets,
    _shifted_lag_correlations,
    circular_shift_pvalue,
    lag_correlation,
)


def brute_curves(x: np.ndarray, y: np.ndarray, lags: list[int], shifts: np.ndarray) -> np.ndarray:
    return np.asarray(
        [[lag_correlation(x, np.roll(y, -int(shift)), lag)[0] for lag in lags] for shift in shifts]
    )


class CircularShiftTests(unittest.TestCase):
    def assert_matches_brute(self, x: np.ndarray, y: np.ndarray, lags: list[int]) -> None:
        shifts = _circular_shift_offsets(len(x), lags)
        expected = brute_curves(x, y, lags, shifts)
        actual = _shifted_lag_correlations(x, y, lags, shifts)
        np.testing.assert_allclose(actual, expected, atol=2e-12, rtol=2e-12, equal_nan=True)
        observed = max(abs(lag_correlation(x, y, lag)[0]) for lag in lags)
        expected_null = np.nanmax(np.abs(expected), axis=1)
        expected_p = (1 + np.sum(expected_null >= observed)) / (len(shifts) + 1)
        actual_p, count = circular_shift_pvalue(x, y, lags, observed)
        self.assertEqual(count, len(shifts))
        self.assertEqual(actual_p, expected_p)

    def test_complete_series(self) -> None:
        rng = np.random.default_rng(17)
        self.assert_matches_brute(rng.normal(size=96), rng.normal(size=96), list(range(-8, 9)))

    def test_local_48_bins_exhausts_23_shifts(self) -> None:
        rng = np.random.default_rng(19)
        lags = list(range(-12, 13))
        np.testing.assert_array_equal(_circular_shift_offsets(48, lags), np.arange(13, 36))
        self.assert_matches_brute(rng.lognormal(size=48), rng.normal(size=48), lags)

    def test_missing_and_infinite_values_keep_time_positions(self) -> None:
        rng = np.random.default_rng(23)
        x, y = rng.normal(size=(2, 81))
        x[[0, 3, 11, 12, 13, 50, 80]] = np.nan
        y[[0, 4, 5, 30, 31, 32, 70]] = np.nan
        x[20], y[60] = np.inf, -np.inf
        self.assert_matches_brute(x, y, list(range(-7, 8)))
        self.assertEqual(len(_circular_shift_offsets(len(x), list(range(-7, 8)))), 66)

    def test_affine_scaling_and_subset_variances(self) -> None:
        rng = np.random.default_rng(29)
        x, y = rng.normal(size=(2, 77))
        self.assert_matches_brute(1000 + 2 * x, -300 + 7 * y, [-6, -2, 0, 3, 6])

    def test_constant_lag_subset_uses_direct_fallback(self) -> None:
        x, y = np.zeros(48), np.zeros(48)
        x[0], y[-1] = 1, 1
        shifts = _circular_shift_offsets(48, list(range(-12, 13)))
        expected = brute_curves(x, y, list(range(-12, 13)), shifts)
        actual = _shifted_lag_correlations(x, y, list(range(-12, 13)), shifts)
        np.testing.assert_allclose(actual, expected, atol=2e-12, rtol=2e-12, equal_nan=True)

    def test_exact_periodic_ties_retain_plus_one_upper_tail(self) -> None:
        x = np.tile([0.0, 1.0, 0.0, -1.0], 16)
        self.assert_matches_brute(x, np.roll(x, 2), [-2, 0, 2])

    def test_deterministic_seed_and_5000_cap(self) -> None:
        lags = list(range(-12, 13))
        expected = np.sort(np.random.default_rng(RNG_SEED).choice(np.arange(13, 8916), 5000, replace=False))
        np.testing.assert_array_equal(_circular_shift_offsets(8928, lags), expected)

    def test_lag_sign_and_shift_direction(self) -> None:
        rng = np.random.default_rng(31)
        x = rng.normal(size=128)
        y = np.roll(x, 5)
        curves = _shifted_lag_correlations(x, y, [-3, 0, 3, 5], np.array([0, 2]))
        self.assertAlmostEqual(curves[0, 3], 1.0)
        self.assertAlmostEqual(curves[1, 2], 1.0)

    def test_untestable_shift_is_not_silently_discarded(self) -> None:
        x, y = np.full(48, np.nan), np.full(48, np.nan)
        x[:10] = y[:10] = np.arange(10)
        p, count = circular_shift_pvalue(x, y, [0], 1.0)
        self.assertTrue(math.isnan(p))
        self.assertEqual(count, 47)

    def test_short_series(self) -> None:
        p, count = circular_shift_pvalue(np.arange(20), np.arange(20), [-1, 0, 1], 1.0)
        self.assertTrue(math.isnan(p))
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
