import random
from threading import Thread

import pytest

from ai_ecosystem_benchmark._latency_histogram import LatencyHistogram


def test_empty_histogram_count_is_zero() -> None:
    assert LatencyHistogram().count() == 0


def test_empty_histogram_percentiles_return_zero() -> None:
    h = LatencyHistogram()
    assert h.percentile_ns(0) == 0
    assert h.percentile_ns(50) == 0
    assert h.percentile_ns(99) == 0
    assert h.percentile_ns(100) == 0


def test_empty_histogram_min_max_default_to_zero() -> None:
    h = LatencyHistogram()
    assert h.min_ns() == 0
    assert h.max_ns() == 0


def test_record_increments_count() -> None:
    h = LatencyHistogram()
    h.record(100)
    h.record(200)
    h.record(300)
    assert h.count() == 3


def test_record_tracks_min_and_max() -> None:
    h = LatencyHistogram()
    h.record(500)
    h.record(100)
    h.record(900)
    h.record(250)
    assert h.min_ns() == 100
    assert h.max_ns() == 900


def test_negative_values_are_ignored() -> None:
    h = LatencyHistogram()
    h.record(-1)
    h.record(-1_000_000)
    h.record(42)
    assert h.count() == 1
    assert h.min_ns() == 42
    assert h.max_ns() == 42


def test_linear_region_is_exact() -> None:
    """The lowest 128 buckets store ns values exactly (no quantization)."""
    h = LatencyHistogram()
    for value in range(128):
        h.record(value)

    # Each ns value 0..127 has its own bucket; percentile lookup should be exact.
    assert h.percentile_ns(50) == 63
    assert h.percentile_ns(99) == 126
    assert h.percentile_ns(100) == 127


def test_percentile_within_one_percent_relative_error_for_log_spread() -> None:
    """For values spread across many orders of magnitude, p50 should be accurate."""
    h = LatencyHistogram()
    rng = random.Random(0)
    values = [rng.randint(1_000, 1_000_000_000) for _ in range(10_000)]
    for value in values:
        h.record(value)

    sorted_values = sorted(values)
    true_p50 = sorted_values[5_000]
    histogram_p50 = h.percentile_ns(50)

    relative_error = abs(histogram_p50 - true_p50) / true_p50
    # Histogram precision is ~0.8% (1 / SUB_BUCKET_COUNT). Allow 1% to account for
    # both bucket quantization and the linear-interpolation gap between sorted samples.
    assert relative_error < 0.01


def test_percentile_100_returns_at_least_the_max_bucket() -> None:
    h = LatencyHistogram()
    for value in (100, 500, 10_000, 250_000):
        h.record(value)

    # p100's bucket lower bound is <= max_ns and within ~0.8% relative error.
    p100 = h.percentile_ns(100)
    assert p100 <= h.max_ns()
    assert (h.max_ns() - p100) / h.max_ns() < 0.01


def test_huge_value_is_clamped_to_last_bucket_without_overflow() -> None:
    """Values beyond the tracked range are clamped, not silently lost."""
    h = LatencyHistogram()
    h.record(2**50)  # well beyond _HIGHEST_TRACKABLE_BITS = 42

    assert h.count() == 1
    # max_ns preserves the actual recorded value even though the bucket is clamped.
    assert h.max_ns() == 2**50
    # Percentile is the clamped bucket's lower bound, which is still huge.
    assert h.percentile_ns(100) > 0


def test_memory_footprint_is_constant_under_high_volume() -> None:
    """The internal counts array does not grow with the number of records."""
    h = LatencyHistogram()
    counts_length_before = len(h._counts)  # noqa: SLF001 - intentional whitebox check

    rng = random.Random(1)
    for _ in range(100_000):
        h.record(rng.randint(1, 10_000_000))

    assert h.count() == 100_000
    assert len(h._counts) == counts_length_before  # noqa: SLF001


def test_concurrent_record_is_thread_safe() -> None:
    """Many threads recording simultaneously must produce the exact total count."""
    h = LatencyHistogram()
    records_per_thread = 5_000
    thread_count = 8

    def record_many(seed: int) -> None:
        rng = random.Random(seed)
        for _ in range(records_per_thread):
            h.record(rng.randint(1, 1_000_000))

    threads = [Thread(target=record_many, args=(i,)) for i in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert h.count() == records_per_thread * thread_count


@pytest.mark.parametrize("value", [128, 256, 1024, 1_000_000])
def test_recorded_value_lands_in_a_bucket_whose_lower_bound_is_at_most_the_value(
    value: int,
) -> None:
    """``percentile_ns(100)`` returns the bucket *lower bound*; it must never exceed
    the recorded value (otherwise we'd be over-reporting latency)."""
    h = LatencyHistogram()
    h.record(value)

    bucket_lower_bound = h.percentile_ns(100)
    assert bucket_lower_bound <= value
    # And within 0.8% relative error of the true value.
    assert (value - bucket_lower_bound) / value < 1 / 128
