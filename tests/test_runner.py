import random
import time
import warnings
from threading import Lock
from typing import Any

import pytest

from ai_ecosystem_benchmark import BaseBenchmarkWorkload, BenchmarkRunner
from ai_ecosystem_benchmark._latency_histogram import LatencyHistogram

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
    # qps * runtime = 6 * 1 = 6 calls per enabled test, ~1s per test.
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

    assert workload.aerospike_calls == 6
    assert workload.redis_calls == 6
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

    # 10 qps * 1s = 10 calls per enabled test; CountingWorkload has aerospike + redis,
    # so total wall-clock is at least ~2s of paced work (with slack on both sides).
    assert workload.aerospike_calls == 10
    assert workload.redis_calls == 10
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

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        runner.run()

    histogram = runner.metrics["aerospike"]["aerospike_test_flaky"]
    # 3 of every 6 calls fail (calls 2, 4, 6). Successes go in the histogram, failures
    # in the failure counter; counts should sum to the total scheduled calls.
    assert histogram.count() == 3
    assert runner.failures["aerospike"]["aerospike_test_flaky"] == 3


def test_runner_surfaces_first_failure_as_warning() -> None:
    workload = AlternatingFailureWorkload()
    runner = BenchmarkRunner(
        queries_per_second=4,
        scheduler_thread_count=1,
        worker_thread_count=2,
        runtime_per_function=1,
        workload=workload,
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        runner.run()

    first_failure_warnings = [w for w in caught if "first failure" in str(w.message)]
    assert len(first_failure_warnings) == 1
    message = str(first_failure_warnings[0].message)
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

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        runner.run()

    assert runner.failures["aerospike"]["aerospike_test_broken"] == 4
    assert runner.metrics["aerospike"]["aerospike_test_broken"].count() == 0


# ---------------------------------------------------------------------------
# Worker pool congestion warning
# ---------------------------------------------------------------------------


def test_runner_warns_when_worker_pool_is_undersized() -> None:
    """A 1-worker pool faced with 20 qps of 300ms tests must surface a congestion warning."""
    workload = SlowWorkload()
    runner = BenchmarkRunner(
        queries_per_second=20,
        scheduler_thread_count=1,
        worker_thread_count=1,
        runtime_per_function=1,
        workload=workload,
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        runner.run()

    congestion_warnings = [w for w in caught if "congestion" in str(w.message)]
    assert len(congestion_warnings) == 1
    message = str(congestion_warnings[0].message)
    assert "worker_thread_count" in message
    assert "aerospike.aerospike_test_slow" in message


def test_runner_does_not_warn_when_worker_pool_is_adequate() -> None:
    """A well-provisioned pool should not trip the congestion warning."""
    workload = CountingWorkload()
    runner = _make_runner(workload)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        runner.run()

    congestion_warnings = [w for w in caught if "congestion" in str(w.message)]
    assert congestion_warnings == []


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
