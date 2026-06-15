import csv
import math
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any

import pytest

from ai_ecosystem_benchmark import BaseBenchmarkWorkload, BenchmarkRunner
from ai_ecosystem_benchmark._latency_histogram import LatencyHistogram
from ai_ecosystem_benchmark.benchmark_runner import _WORKERS_PER_SHARD, _prewarm_pool

# ---------------------------------------------------------------------------
# Helper workloads
# ---------------------------------------------------------------------------


class CountingWorkload(BaseBenchmarkWorkload):
    """Workload that just counts how many times each test is invoked."""

    def __init__(self) -> None:
        super().__init__(
            aerospike_connection_string="aerospike://localhost:3000",
            redis_connection_string="redis://localhost:6379/0",
        )
        self.aerospike_calls = 0
        self.redis_calls = 0
        self.postgres_calls = 0
        self.lifecycle_events: list[str] = []
        self._lock = Lock()

    def setup(self) -> None:
        self.lifecycle_events.append("setup")

    def between_benchmarks(self) -> None:
        self.lifecycle_events.append("between_benchmarks")

    def teardown(self) -> None:
        self.lifecycle_events.append("teardown")

    def aerospike_test_query(self) -> None:
        with self._lock:
            self.aerospike_calls += 1

    def postgres_test_query(self) -> None:
        with self._lock:
            self.postgres_calls += 1

    def redis_test_query(self) -> None:
        with self._lock:
            self.redis_calls += 1


class AlternatingFailureWorkload(BaseBenchmarkWorkload):
    """Workload whose test fails on every other call with a specific exception."""

    def __init__(self) -> None:
        super().__init__(aerospike_connection_string="aerospike://localhost:3000")
        self.call_count = 0
        self._lock = Lock()

    def setup(self) -> None:
        return None

    def between_benchmarks(self) -> None:
        return None

    def teardown(self) -> None:
        return None

    def aerospike_test_flaky(self) -> None:
        with self._lock:
            self.call_count += 1
            should_fail = self.call_count % 2 == 0
        if should_fail:
            raise RuntimeError("simulated backend failure")


class SlowWorkload(BaseBenchmarkWorkload):
    """Workload whose test sleeps long enough to saturate a 1-worker pool."""

    def __init__(self) -> None:
        super().__init__(aerospike_connection_string="aerospike://localhost:3000")

    def setup(self) -> None:
        return None

    def between_benchmarks(self) -> None:
        return None

    def teardown(self) -> None:
        return None

    def aerospike_test_slow(self) -> None:
        time.sleep(0.3)


def _make_runner(workload: BaseBenchmarkWorkload) -> BenchmarkRunner:
    # qps * runtime = 6 * 1 = 6 timed calls per enabled test, ~1s per test. The mandatory
    # warmup probe runs another min(64, 6) = 6 (unrecorded) calls first, so a counting
    # workload sees 12 invocations but the histogram only records the 6 timed calls.
    return BenchmarkRunner(
        queries_per_second=6,
        scheduler_thread_count=2,
        worker_thread_count=4,
        runtime_per_function=1,
        workload=workload,
    )


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "override,error_substring",
    [
        ({"queries_per_second": 0}, "queries_per_second"),
        ({"queries_per_second": -1}, "queries_per_second"),
        ({"scheduler_thread_count": 0}, "scheduler_thread_count"),
        ({"worker_thread_count": 0}, "worker_thread_count"),
        ({"runtime_per_function": 0}, "runtime_per_function"),
        ({"runtime_per_function": -5}, "runtime_per_function"),
        ({"metrics_window_seconds": 0}, "metrics_window_seconds"),
        ({"metrics_window_seconds": -1}, "metrics_window_seconds"),
    ],
)
def test_runner_rejects_non_positive_params(override: dict[str, Any], error_substring: str) -> None:
    base: dict[str, Any] = {
        "queries_per_second": 10,
        "scheduler_thread_count": 1,
        "worker_thread_count": 1,
        "runtime_per_function": 1,
        "workload": CountingWorkload(),
    }
    base.update(override)
    with pytest.raises(ValueError, match=error_substring):
        BenchmarkRunner(**base)


# ---------------------------------------------------------------------------
# Core execution behavior
# ---------------------------------------------------------------------------


def test_runner_runs_enabled_tests_at_configured_load() -> None:
    workload = CountingWorkload()
    runner = _make_runner(workload)

    runner.run()

    # 6 warmup-probe calls + 6 timed calls per enabled test; postgres is disabled so it is
    # never probed or run.
    assert workload.aerospike_calls == 12
    assert workload.redis_calls == 12
    assert workload.postgres_calls == 0


def test_runner_records_per_call_latencies_in_histogram() -> None:
    workload = CountingWorkload()
    runner = _make_runner(workload)

    runner.run()

    aerospike_hist = runner.metrics["aerospike"]["aerospike_test_query"]
    redis_hist = runner.metrics["redis"]["redis_test_query"]
    assert isinstance(aerospike_hist, LatencyHistogram)
    assert aerospike_hist.count() == 6
    assert aerospike_hist.min_ns() >= 0
    assert redis_hist.count() == 6
    assert "postgres" not in runner.metrics


def test_runner_records_service_latency_alongside_response_latency() -> None:
    """Service latency (execution only) is recorded per call and never exceeds response."""
    workload = CountingWorkload()
    runner = _make_runner(workload)

    runner.run()

    response_hist = runner.metrics["aerospike"]["aerospike_test_query"]
    service_hist = runner.service_metrics["aerospike"]["aerospike_test_query"]
    assert isinstance(service_hist, LatencyHistogram)
    # Same successful calls feed both histograms.
    assert service_hist.count() == response_hist.count() == 6
    # Service latency excludes queue wait, so it cannot exceed response latency.
    assert service_hist.percentile_ns(50) <= response_hist.percentile_ns(99) + 1
    assert "postgres" not in runner.service_metrics


def test_runner_invokes_lifecycle_hooks() -> None:
    workload = CountingWorkload()
    runner = BenchmarkRunner(
        queries_per_second=1,
        scheduler_thread_count=1,
        worker_thread_count=1,
        runtime_per_function=1,
        workload=workload,
    )

    runner.run()

    assert workload.lifecycle_events[0] == "setup"
    assert workload.lifecycle_events[-1] == "teardown"
    assert "between_benchmarks" in workload.lifecycle_events


def test_runner_paces_calls_at_configured_qps() -> None:
    """Scheduler should spread the configured number of calls over ~runtime_per_function."""
    workload = CountingWorkload()
    runner = BenchmarkRunner(
        queries_per_second=10,
        scheduler_thread_count=2,
        worker_thread_count=8,
        runtime_per_function=1,
        workload=workload,
    )

    start = time.perf_counter()
    runner.run()
    elapsed = time.perf_counter() - start

    # 10 qps * 1s = 10 *timed* calls per enabled test (only these are recorded); the warmup
    # probe runs 10 more unrecorded calls first. CountingWorkload has aerospike + redis, so
    # the paced (timed) portion alone is at least ~2s of wall-clock.
    assert runner.metrics["aerospike"]["aerospike_test_query"].count() == 10
    assert runner.metrics["redis"]["redis_test_query"].count() == 10
    assert elapsed >= 1.8


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


def test_runner_counts_failures_and_excludes_them_from_histogram() -> None:
    workload = AlternatingFailureWorkload()
    runner = BenchmarkRunner(
        queries_per_second=6,
        scheduler_thread_count=1,
        worker_thread_count=2,
        runtime_per_function=1,
        workload=workload,
    )

    runner.run()

    histogram = runner.metrics["aerospike"]["aerospike_test_flaky"]
    # The warmup probe consumes 6 calls first (failures ignored, not recorded); the 6 timed
    # calls then alternate, so 3 succeed (recorded) and 3 fail (counted). Probe failures never
    # reach the failure counter.
    assert histogram.count() == 3
    assert runner.failures["aerospike"]["aerospike_test_flaky"] == 3


def test_runner_surfaces_first_failure_as_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    workload = AlternatingFailureWorkload()
    runner = BenchmarkRunner(
        queries_per_second=4,
        scheduler_thread_count=1,
        worker_thread_count=2,
        runtime_per_function=1,
        workload=workload,
    )

    runner.run()

    message = capsys.readouterr().out
    assert message.count("first failure") == 1
    assert "RuntimeError" in message
    assert "simulated backend failure" in message


def test_runner_does_not_crash_when_all_calls_fail() -> None:
    """A workload whose test always raises should still complete cleanly."""

    class AlwaysFailingWorkload(BaseBenchmarkWorkload):
        def __init__(self) -> None:
            super().__init__(aerospike_connection_string="aerospike://localhost:3000")

        def setup(self) -> None:
            return None

        def between_benchmarks(self) -> None:
            return None

        def teardown(self) -> None:
            return None

        def aerospike_test_broken(self) -> None:
            raise ValueError("always broken")

    runner = BenchmarkRunner(
        queries_per_second=4,
        scheduler_thread_count=1,
        worker_thread_count=2,
        runtime_per_function=1,
        workload=AlwaysFailingWorkload(),
    )

    runner.run()

    assert runner.failures["aerospike"]["aerospike_test_broken"] == 4
    assert runner.metrics["aerospike"]["aerospike_test_broken"].count() == 0


# ---------------------------------------------------------------------------
# Worker pool congestion warning
# ---------------------------------------------------------------------------


def test_runner_warns_when_worker_pool_is_undersized(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A 1-worker pool faced with 10 qps of 300ms tests must report a *saturated* pool.

    worker_thread_count=1 caps the pool at one thread, so the warmup sizes (and pre-warms)
    exactly one worker -- exercising the cap-saturation path.
    """
    workload = SlowWorkload()
    runner = BenchmarkRunner(
        queries_per_second=10,
        scheduler_thread_count=1,
        worker_thread_count=1,
        runtime_per_function=1,
        workload=workload,
    )

    runner.run()

    message = capsys.readouterr().out
    assert message.count("WARNING:") == 1
    # Genuine starvation at the cap: raising worker_thread_count is the fix.
    assert "cap" in message
    assert "worker_thread_count" in message
    assert "aerospike.aerospike_test_slow" in message


def test_runner_does_not_warn_when_worker_pool_is_adequate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A well-provisioned pool should not trip the congestion warning."""
    workload = CountingWorkload()
    runner = _make_runner(workload)

    runner.run()

    assert "WARNING:" not in capsys.readouterr().out


def _congestion_runner() -> BenchmarkRunner:
    """A runner with a deliberately huge worker pool, like the field 8192-thread config."""
    return BenchmarkRunner(
        queries_per_second=500,
        scheduler_thread_count=8,
        worker_thread_count=8192,
        runtime_per_function=20,
        workload=CountingWorkload(),
    )


def _dummy_test() -> None:
    return None


_THRESHOLD_NS = 50_000_000  # 50 ms floor, matching the runner default at these settings.


def test_single_dispatch_lag_blip_does_not_warn(capsys: pytest.CaptureFixture[str]) -> None:
    """The reported false positive: one 50.5ms pickup is noise, not congestion."""
    runner = _congestion_runner()

    runner._maybe_warn_congestion(
        backend="aerospike",
        test=_dummy_test,
        total_calls=10_000,
        congestion_threshold_ns=_THRESHOLD_NS,
        pool_size=8192,
        achieved_qps=500,  # kept up fine
        peak_in_flight=80,  # nowhere near the pool size -> not starvation
        lag_count=1,  # a single blip -> below the sustained floor
        worst_lag_ns=50_500_000,
    )

    assert "WARNING:" not in capsys.readouterr().out


def test_throughput_shortfall_with_idle_pool_blames_downstream_not_workers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The cyclic signature: huge backlog while the pool sits idle -> downstream bottleneck."""
    runner = _congestion_runner()

    runner._maybe_warn_congestion(
        backend="aerospike",
        test=_dummy_test,
        total_calls=10_000,
        congestion_threshold_ns=_THRESHOLD_NS,
        pool_size=286,
        achieved_qps=49,  # ~10% of the 500 target -> could not keep up
        peak_in_flight=31,  # pool ~90% idle the whole time
        lag_count=9_989,
        worst_lag_ns=205_941_700_000,
    )

    message = capsys.readouterr().out
    assert message.count("WARNING:") == 1
    assert "worker pool idle" in message
    assert "backend or DB connection pool cannot sustain" in message
    # Must NOT misattribute to jitter as the old message did.
    assert "jitter" not in message


def test_transient_lag_when_throughput_is_met_points_at_jitter(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Sustained lag but the run still kept up + pool idle -> genuine transient jitter."""
    runner = _congestion_runner()

    runner._maybe_warn_congestion(
        backend="aerospike",
        test=_dummy_test,
        total_calls=10_000,
        congestion_threshold_ns=_THRESHOLD_NS,
        pool_size=8192,
        achieved_qps=495,  # met the 500 target
        peak_in_flight=80,  # pool never saturated
        lag_count=200,  # 2% of calls -> sustained, above the 1% floor
        worst_lag_ns=80_000_000,
    )

    message = capsys.readouterr().out
    assert message.count("WARNING:") == 1
    assert "target qps met" in message
    assert "jitter" in message


def test_saturated_pool_at_ceiling_warns_to_increase_workers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When peak in-flight reaches the pool ceiling, raising worker_thread_count is the fix."""
    runner = _congestion_runner()

    runner._maybe_warn_congestion(
        backend="aerospike",
        test=_dummy_test,
        total_calls=10_000,
        congestion_threshold_ns=_THRESHOLD_NS,
        pool_size=8192,  # pool ceiling (e.g. calibrate=False uses worker_thread_count)
        achieved_qps=300,  # fell behind because the pool was the limit
        peak_in_flight=8192,  # hit the ceiling -> genuine client thread starvation
        lag_count=500,
        worst_lag_ns=120_000_000,
    )

    message = capsys.readouterr().out
    assert message.count("WARNING:") == 1
    assert "cap" in message
    assert "worker_thread_count" in message


def test_sized_pool_saturated_below_ceiling_but_near_target_blames_calibration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Saturated below cap while nearly meeting target -> the probe under-estimated latency."""
    runner = _congestion_runner()

    runner._maybe_warn_congestion(
        backend="aerospike",
        test=_dummy_test,
        total_calls=10_000,
        congestion_threshold_ns=_THRESHOLD_NS,
        pool_size=256,  # sized below the 8192 cap
        achieved_qps=480,  # nearly met the 500 target -> just a touch undersized
        peak_in_flight=256,  # but still saturated under load
        lag_count=500,
        worst_lag_ns=120_000_000,
    )

    message = capsys.readouterr().out
    assert message.count("WARNING:") == 1
    assert "saturated below cap" in message
    assert "under-sized the pool" in message
    # The cap was never the limit, so the message must not mention the cap knob at all.
    assert "worker_thread_count" not in message


def test_sized_pool_saturated_below_ceiling_with_low_throughput_blames_backend(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The postgres_graph_cyclic signature: saturated below cap, throughput far below target.

    Every worker is busy yet only a fraction of the target qps lands, so per-call latency
    grew under load. Adding workers cannot help -- the backend/DB is the ceiling.
    """
    runner = _congestion_runner()

    runner._maybe_warn_congestion(
        backend="postgres",
        test=_dummy_test,
        total_calls=10_000,
        congestion_threshold_ns=_THRESHOLD_NS,
        pool_size=722,  # sized below the 8192 cap
        achieved_qps=32,  # ~6% of the 500 target despite a saturated pool
        peak_in_flight=722,  # every worker busy the whole run
        lag_count=9_000,
        worst_lag_ns=273_523_000_000,
    )

    message = capsys.readouterr().out
    assert message.count("WARNING:") == 1
    assert "backend or DB connection pool" in message
    assert "latency grew under load" in message
    # The backend is the cause, so the message must not mention the worker cap knob.
    assert "worker_thread_count" not in message


def test_no_lag_never_warns(capsys: pytest.CaptureFixture[str]) -> None:
    """No dispatch lag at all means no warning, regardless of pool size."""
    runner = _congestion_runner()

    runner._maybe_warn_congestion(
        backend="aerospike",
        test=_dummy_test,
        total_calls=10_000,
        congestion_threshold_ns=_THRESHOLD_NS,
        pool_size=8192,
        achieved_qps=500,
        peak_in_flight=80,
        lag_count=0,
        worst_lag_ns=0,
    )

    assert "WARNING:" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Worker-pool pre-warming and right-sizing
# ---------------------------------------------------------------------------


class FixedLatencyWorkload(BaseBenchmarkWorkload):
    """Workload whose aerospike test sleeps for a fixed duration (for calibration tests)."""

    def __init__(self, sleep_s: float) -> None:
        super().__init__(aerospike_connection_string="aerospike://localhost:3000")
        self._sleep_s = sleep_s

    def setup(self) -> None:
        return None

    def between_benchmarks(self) -> None:
        return None

    def teardown(self) -> None:
        return None

    def aerospike_test_fixed(self) -> None:
        time.sleep(self._sleep_s)


def _calibrating_runner(
    workload: BaseBenchmarkWorkload, worker_thread_count: int
) -> BenchmarkRunner:
    return BenchmarkRunner(
        queries_per_second=50,
        scheduler_thread_count=2,
        worker_thread_count=worker_thread_count,
        runtime_per_function=1,
        workload=workload,
    )


def test_prewarm_pool_materializes_all_worker_threads() -> None:
    """Pre-warming must create every worker thread up front, not lazily on submit."""
    with ThreadPoolExecutor(max_workers=16) as pool:
        assert len(pool._threads) == 0
        _prewarm_pool(pool, 16)
        assert len(pool._threads) == 16


def test_shard_layout_single_shard_below_threshold() -> None:
    """A pool at or below the per-shard bound stays a single shard (unsharded behavior)."""
    runner = _congestion_runner()

    assert runner._shard_layout(0) == []
    assert runner._shard_layout(1) == [1]
    assert runner._shard_layout(_WORKERS_PER_SHARD) == [_WORKERS_PER_SHARD]


def test_shard_layout_splits_large_pools_and_sums_exactly() -> None:
    """Large pools split into shards of <= _WORKERS_PER_SHARD that sum to the pool size."""
    runner = _congestion_runner()

    for pool_size in (_WORKERS_PER_SHARD + 1, 298, 669, 1000):
        layout = runner._shard_layout(pool_size)
        # Every shard is within the bound, none is empty, and the total is preserved exactly
        # (so we never pre-warm more threads than the cap allows).
        assert sum(layout) == pool_size
        assert all(0 < size <= _WORKERS_PER_SHARD for size in layout)
        assert len(layout) == math.ceil(pool_size / _WORKERS_PER_SHARD)
        # Shard sizes are balanced: they differ by at most one thread.
        assert max(layout) - min(layout) <= 1


def test_warmup_sizes_pool_far_below_a_huge_cap() -> None:
    """A fast op against an 8192 cap should be sized down to a small pool by the warmup."""
    workload = FixedLatencyWorkload(sleep_s=0.02)
    runner = _calibrating_runner(workload, worker_thread_count=8192)

    pool_size = runner._size_worker_pool("aerospike", workload.aerospike_test_fixed, total_calls=64)

    # Little's Law (50 qps x ~20 ms x headroom) is tiny, so we land on the floor -- the
    # point is that it's nowhere near the cap.
    assert 32 <= pool_size <= 256


def test_warmup_respects_the_configured_cap() -> None:
    """Sizing never exceeds worker_thread_count, even for a slow, high-qps op."""
    workload = FixedLatencyWorkload(sleep_s=0.05)
    runner = _calibrating_runner(workload, worker_thread_count=4)

    pool_size = runner._size_worker_pool("aerospike", workload.aerospike_test_fixed, total_calls=16)

    assert pool_size == 4


def test_warmup_falls_back_when_every_probe_call_fails() -> None:
    """If latency can't be estimated, fall back to a bounded count, not the raw cap."""

    def always_fails() -> None:
        raise RuntimeError("probe failure")

    always_fails.__name__ = "aerospike_test_probe_fail"
    workload = FixedLatencyWorkload(sleep_s=0.0)
    runner = _calibrating_runner(workload, worker_thread_count=8192)

    pool_size = runner._size_worker_pool("aerospike", always_fails, total_calls=8)

    assert pool_size == 256  # _CALIBRATION_FALLBACK_WORKERS, clamped to the cap


def test_pool_size_is_capped_at_worker_thread_count() -> None:
    """The single knob bounds the (fully pre-warmed) pool, preventing the pre-warm OOM.

    postgres_graph_cyclic's probe estimated ~3000 workers; pre-warming all of them tripped
    a thread/mapping limit. Clamping the pool to worker_thread_count keeps pre-warm bounded.
    """
    workload = FixedLatencyWorkload(sleep_s=0.0)
    runner = _calibrating_runner(workload, worker_thread_count=512)

    assert runner._clamp_pool_size(3105) == 512
    assert runner._clamp_pool_size(10_000) == 512
    # Values between the floor and the cap pass through unchanged.
    assert runner._clamp_pool_size(100) == 100


def test_small_worker_thread_count_binds_the_pool_and_the_floor() -> None:
    """A worker_thread_count below the min-threads floor still binds the pool to itself."""
    workload = FixedLatencyWorkload(sleep_s=0.0)
    runner = _calibrating_runner(workload, worker_thread_count=64)

    assert runner._clamp_pool_size(10_000) == 64
    # The floor is min(_MIN_WORKER_THREADS, cap) = min(32, 64) = 32.
    assert runner._clamp_pool_size(10) == 32


# ---------------------------------------------------------------------------
# Windowed metrics + CSV export
# ---------------------------------------------------------------------------


class SingleAerospikeWorkload(BaseBenchmarkWorkload):
    """Workload with a single aerospike test for focused windowed-metrics tests."""

    def __init__(self) -> None:
        super().__init__(aerospike_connection_string="aerospike://localhost:3000")
        self.call_count = 0
        self._lock = Lock()

    def setup(self) -> None:
        return None

    def between_benchmarks(self) -> None:
        return None

    def teardown(self) -> None:
        return None

    def aerospike_test_only(self) -> None:
        with self._lock:
            self.call_count += 1


class SlowFirstCallWorkload(BaseBenchmarkWorkload):
    """First call sleeps past the runtime window; later calls are instant."""

    def __init__(self, sleep_seconds: float) -> None:
        super().__init__(aerospike_connection_string="aerospike://localhost:3000")
        self.sleep_seconds = sleep_seconds
        self.call_count = 0
        self._lock = Lock()

    def setup(self) -> None:
        return None

    def between_benchmarks(self) -> None:
        return None

    def teardown(self) -> None:
        return None

    def aerospike_test_slow_first(self) -> None:
        with self._lock:
            self.call_count += 1
            is_first = self.call_count == 1
        if is_first:
            time.sleep(self.sleep_seconds)


_CSV_HEADER = [
    "backend",
    "test",
    "window_start_sec",
    "window_seconds",
    "calls",
    "failures",
    "calls_per_sec",
    "p50_ms",
    "p90_ms",
    "p99_ms",
]


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def test_windowed_metrics_writes_csv_with_expected_schema(tmp_path: Path) -> None:
    workload = SingleAerospikeWorkload()
    csv_dir = tmp_path / "metrics"
    runner = BenchmarkRunner(
        queries_per_second=4,
        scheduler_thread_count=1,
        worker_thread_count=4,
        runtime_per_function=2,
        workload=workload,
        metrics_window_seconds=1,
        csv_output_path=str(csv_dir),
    )

    runner.run()

    csv_path = csv_dir / "aerospike_aerospike_test_only.csv"
    rows = _read_csv_rows(csv_path)
    assert list(rows[0].keys()) == _CSV_HEADER
    assert len(rows) == 2
    assert rows[0]["backend"] == "aerospike"
    assert rows[0]["test"] == "aerospike_test_only"
    assert rows[0]["window_start_sec"] == "0"
    assert rows[1]["window_start_sec"] == "1"
    assert rows[0]["window_seconds"] == "1"
    timed_calls = runner.metrics["aerospike"]["aerospike_test_only"].count()
    assert int(rows[0]["calls"]) + int(rows[1]["calls"]) == timed_calls
    assert timed_calls == 8


def test_windowed_metrics_default_csv_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    workload = SingleAerospikeWorkload()
    runner = BenchmarkRunner(
        queries_per_second=2,
        scheduler_thread_count=1,
        worker_thread_count=2,
        runtime_per_function=1,
        workload=workload,
        metrics_window_seconds=1,
    )

    runner.run()

    default_path = tmp_path / "benchmark_metrics_windows" / "aerospike_aerospike_test_only.csv"
    assert default_path.exists()
    rows = _read_csv_rows(default_path)
    assert len(rows) == 1


def test_windowed_metrics_buckets_failures_by_completion_window(tmp_path: Path) -> None:
    workload = AlternatingFailureWorkload()
    csv_dir = tmp_path / "metrics"
    runner = BenchmarkRunner(
        queries_per_second=6,
        scheduler_thread_count=1,
        worker_thread_count=2,
        runtime_per_function=1,
        workload=workload,
        metrics_window_seconds=1,
        csv_output_path=str(csv_dir),
    )

    runner.run()

    csv_path = csv_dir / "aerospike_aerospike_test_flaky.csv"
    rows = _read_csv_rows(csv_path)
    assert len(rows) == 1
    assert int(rows[0]["calls"]) == 3
    assert int(rows[0]["failures"]) == 3
    assert int(rows[0]["calls_per_sec"]) == 6
    histogram = runner.metrics["aerospike"]["aerospike_test_flaky"]
    assert histogram.count() == 3
    assert runner.failures["aerospike"]["aerospike_test_flaky"] == 3


def test_windowed_metrics_clamps_late_completions_to_last_window(tmp_path: Path) -> None:
    workload = SlowFirstCallWorkload(sleep_seconds=1.5)
    csv_dir = tmp_path / "metrics"
    runner = BenchmarkRunner(
        queries_per_second=2,
        scheduler_thread_count=1,
        worker_thread_count=1,
        runtime_per_function=1,
        workload=workload,
        metrics_window_seconds=1,
        csv_output_path=str(csv_dir),
    )

    runner.run()

    csv_path = csv_dir / "aerospike_aerospike_test_slow_first.csv"
    rows = _read_csv_rows(csv_path)
    assert len(rows) == 1
    assert int(rows[0]["calls"]) == 2
    assert runner.metrics["aerospike"]["aerospike_test_slow_first"].count() == 2


def test_windowed_metrics_writes_separate_csv_per_method(tmp_path: Path) -> None:
    workload = CountingWorkload()
    csv_dir = tmp_path / "metrics"
    runner = BenchmarkRunner(
        queries_per_second=4,
        scheduler_thread_count=1,
        worker_thread_count=4,
        runtime_per_function=1,
        workload=workload,
        metrics_window_seconds=1,
        csv_output_path=str(csv_dir),
    )

    runner.run()

    aerospike_csv = csv_dir / "aerospike_aerospike_test_query.csv"
    redis_csv = csv_dir / "redis_redis_test_query.csv"
    assert aerospike_csv.exists()
    assert redis_csv.exists()
    assert not (csv_dir / "postgres_postgres_test_query.csv").exists()
    assert len(_read_csv_rows(aerospike_csv)) == 1
    assert len(_read_csv_rows(redis_csv)) == 1


def test_windowed_metrics_disabled_does_not_create_csv(tmp_path: Path) -> None:
    workload = SingleAerospikeWorkload()
    csv_dir = tmp_path / "metrics"
    runner = BenchmarkRunner(
        queries_per_second=2,
        scheduler_thread_count=1,
        worker_thread_count=2,
        runtime_per_function=1,
        workload=workload,
        csv_output_path=str(csv_dir),
    )

    runner.run()

    assert not csv_dir.exists()
    assert runner.metrics["aerospike"]["aerospike_test_only"].count() == 2


# ---------------------------------------------------------------------------
# Demo / smoke test for the full pipeline
# ---------------------------------------------------------------------------


class RandomLatencyWorkload(BaseBenchmarkWorkload):
    """Workload where each test sleeps for a small random amount of time."""

    def __init__(self) -> None:
        super().__init__(
            aerospike_connection_string="aerospike://localhost:3000",
            postgres_connection_string="postgresql://bench:password@localhost:5432/bench",
            redis_connection_string="redis://localhost:6379/0",
        )
        self._rng = random.Random(42)
        self._rng_lock = Lock()

    def setup(self) -> None:
        return None

    def between_benchmarks(self) -> None:
        return None

    def teardown(self) -> None:
        return None

    def _sleep_random(self, low_ms: int, high_ms: int) -> None:
        with self._rng_lock:
            sleep_ms = self._rng.randint(low_ms, high_ms)
        time.sleep(sleep_ms / 1000)

    def aerospike_test_insert(self) -> None:
        self._sleep_random(1, 10)

    def aerospike_test_query(self) -> None:
        self._sleep_random(1, 5)

    def postgres_test_query(self) -> None:
        self._sleep_random(5, 20)


def test_print_metrics_demo_with_random_latencies() -> None:
    """Run a small benchmark with random latencies and print the metrics.

    Run with `uv run pytest tests/test_runner.py::test_print_metrics_demo_with_random_latencies -s`
    to see the printed output.
    """
    workload = RandomLatencyWorkload()
    runner = BenchmarkRunner(
        queries_per_second=20,
        scheduler_thread_count=2,
        worker_thread_count=8,
        runtime_per_function=1,
        workload=workload,
    )

    runner.run()
    runner.print_metrics()

    for backend_tests in runner.metrics.values():
        for histogram in backend_tests.values():
            assert histogram.count() == 20
            assert histogram.min_ns() >= 0
            # p50 should be at least the minimum, and at most the maximum.
            assert histogram.min_ns() <= histogram.percentile_ns(50) <= histogram.max_ns()
