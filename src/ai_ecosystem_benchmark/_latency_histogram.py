"""Fixed-size log-linear histogram for nanosecond latency measurements.

This module is private to the package: import via the public surface in
:mod:`ai_ecosystem_benchmark.benchmark_runner` (e.g. via ``runner.metrics``) rather
than reaching in here directly.

Loosely modeled on `HdrHistogram <http://hdrhistogram.org/>`_: linear sub-buckets within
each power-of-two range of nanoseconds. ``record`` is O(1) and uses constant memory
regardless of how many values are recorded, which is what we need for long high-qps
benchmarks where storing every individual latency would explode RAM. ``percentile_ns``
is O(_COUNTS_LENGTH) but only runs at report time, off the hot path.

The maximum relative error of any recorded value compared to the bucket it lands in
is bounded by ``1 / _SUB_BUCKET_COUNT`` (~0.8% with the current settings).
"""

from threading import Lock

_SUB_BUCKET_BITS = 7
_SUB_BUCKET_COUNT = 1 << _SUB_BUCKET_BITS  # 128 sub-buckets per power-of-two range
_SUB_BUCKET_HALF = _SUB_BUCKET_COUNT >> 1  # 64
# Track values up to 2^_HIGHEST_TRACKABLE_BITS ns. 42 bits is ~73 minutes per call --
# anything beyond that gets clamped to the last bucket rather than overflowing.
_HIGHEST_TRACKABLE_BITS = 42
_BUCKET_COUNT = _HIGHEST_TRACKABLE_BITS - _SUB_BUCKET_BITS + 1
# Layout: bucket 0 is the linear [0, 128) range (full 128 slots). Each subsequent
# bucket adds _SUB_BUCKET_HALF (64) new slots, since the lower half of each bucket's
# nominal range overlaps with the previous bucket and is not re-stored.
_COUNTS_LENGTH = _SUB_BUCKET_COUNT + (_BUCKET_COUNT - 1) * _SUB_BUCKET_HALF


class LatencyHistogram:
    """Thread-safe log-linear histogram of nanosecond values."""

    __slots__ = ("_counts", "_count", "_min_ns", "_max_ns", "_lock")

    def __init__(self) -> None:
        self._counts: list[int] = [0] * _COUNTS_LENGTH
        self._count = 0
        self._min_ns = 0
        self._max_ns = 0
        self._lock = Lock()

    def record(self, value_ns: int) -> None:
        """Record a single nanosecond value. Negative values are silently dropped."""
        if value_ns < 0:
            return
        index = _counts_index(value_ns)
        with self._lock:
            self._counts[index] += 1
            if self._count == 0 or value_ns < self._min_ns:
                self._min_ns = value_ns
            if value_ns > self._max_ns:
                self._max_ns = value_ns
            self._count += 1

    def count(self) -> int:
        return self._count

    def min_ns(self) -> int:
        return self._min_ns

    def max_ns(self) -> int:
        return self._max_ns

    def percentile_ns(self, percentile: float) -> int:
        """Return the bucket lower bound for the given percentile in ``[0, 100]``."""
        with self._lock:
            counts_snapshot = self._counts.copy()
            total = self._count
            max_value = self._max_ns
        if total == 0:
            return 0
        target = max(1, min(total, round((percentile / 100.0) * total)))
        cumulative = 0
        for index, count in enumerate(counts_snapshot):
            cumulative += count
            if cumulative >= target:
                return _value_at_index(index)
        return max_value


def _counts_index(value_ns: int) -> int:
    if value_ns < _SUB_BUCKET_COUNT:
        return value_ns
    bucket_index = value_ns.bit_length() - _SUB_BUCKET_BITS
    if bucket_index >= _BUCKET_COUNT:
        return _COUNTS_LENGTH - 1
    sub_bucket_index = value_ns >> bucket_index
    return bucket_index * _SUB_BUCKET_HALF + sub_bucket_index


def _value_at_index(counts_index: int) -> int:
    if counts_index < _SUB_BUCKET_COUNT:
        return counts_index
    bucket_index = (counts_index - _SUB_BUCKET_HALF) // _SUB_BUCKET_HALF
    sub_bucket_index = counts_index - bucket_index * _SUB_BUCKET_HALF
    return sub_bucket_index << bucket_index
