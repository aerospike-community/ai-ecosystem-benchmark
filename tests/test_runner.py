import random
import time
from threading import Lock

from ai_ecosystem_benchmark import BaseBenchmarkWorkload, BenchmarkRunner


class CountingWorkload(BaseBenchmarkWorkload):
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


def test_runner_runs_enabled_tests_thread_count_times_queries_per_second() -> None:
    workload = CountingWorkload()
    runner = BenchmarkRunner(thread_count=2, queries_per_second=3, workload=workload)

    runner.run()

    assert workload.aerospike_calls == 6
    assert workload.redis_calls == 6
    assert workload.postgres_calls == 0


def test_runner_records_per_call_durations_in_metrics() -> None:
    workload = CountingWorkload()
    runner = BenchmarkRunner(thread_count=2, queries_per_second=3, workload=workload)

    runner.run()

    assert list(runner.metrics["aerospike"]["aerospike_test_query"]) and all(
        duration >= 0 for duration in runner.metrics["aerospike"]["aerospike_test_query"]
    )
    assert len(runner.metrics["aerospike"]["aerospike_test_query"]) == 6
    assert len(runner.metrics["redis"]["redis_test_query"]) == 6
    assert "postgres" not in runner.metrics


def test_runner_invokes_lifecycle_hooks() -> None:
    workload = CountingWorkload()
    runner = BenchmarkRunner(thread_count=1, queries_per_second=1, workload=workload)

    runner.run()

    assert workload.lifecycle_events[0] == "setup"
    assert workload.lifecycle_events[-1] == "teardown"
    assert "between_benchmarks" in workload.lifecycle_events


class RandomLatencyWorkload(BaseBenchmarkWorkload):
    """Dummy workload where each test sleeps for a small random amount of time."""

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
    runner = BenchmarkRunner(thread_count=2, queries_per_second=10, workload=workload)

    runner.run()
    runner.print_metrics()

    for backend_tests in runner.metrics.values():
        for durations in backend_tests.values():
            assert len(durations) == 20
            assert all(duration >= 0 for duration in durations)
