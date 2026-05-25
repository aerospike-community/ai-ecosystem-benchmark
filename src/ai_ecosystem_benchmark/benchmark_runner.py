"""Benchmark runner."""

import asyncio
import time
from collections import defaultdict

from ai_ecosystem_benchmark.base_benchmark_workload import BaseBenchmarkWorkload, BenchmarkTest

_BACKENDS = ("aerospike", "postgres", "redis")


class BenchmarkRunner:
    """Coordinates benchmark execution for a workload."""

    def __init__(
        self,
        thread_count: int,
        queries_per_second: int,
        workload: BaseBenchmarkWorkload,
    ) -> None:
        self.thread_count = thread_count
        self.queries_per_second = queries_per_second
        self.workload = workload
        # Per-call latencies are stored as whole milliseconds.
        self.metrics: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))

    def run(self) -> None:
        """Run all enabled benchmark tests against their backends."""
        self.workload.setup()
        try:
            tests_by_backend: dict[str, list[BenchmarkTest]] = {
                "aerospike": self.workload.get_aerospike_tests(),
                "postgres": self.workload.get_postgres_tests(),
                "redis": self.workload.get_redis_tests(),
            }

            for backend, tests in tests_by_backend.items():
                for test in tests:
                    self._run_test(backend, test)
                    self.workload.between_benchmarks()
        finally:
            self.workload.teardown()

    def print_metrics(self) -> None:
        """Print collected per-test latency metrics (in milliseconds) to stdout."""
        print("\n=== Benchmark Metrics (ms) ===")
        for backend in _BACKENDS:
            print(f"\n[{backend}]")
            tests = self.metrics.get(backend, {})
            if not tests:
                print("  (no tests run)")
                continue
            for test_name, durations in tests.items():
                p50 = self._percentile(durations, 50)
                p90 = self._percentile(durations, 90)
                p99 = self._percentile(durations, 99)
                print(
                    f"  {test_name}: calls={len(durations)}  p50={p50}ms  p90={p90}ms  p99={p99}ms"
                )

    def _run_test(self, backend: str, test: BenchmarkTest) -> None:
        call_count = self.thread_count * self.queries_per_second
        print(f"Running {backend}.{test.__name__} with {call_count} calls")
        durations = asyncio.run(self._gather_calls(test, call_count))
        self.metrics[backend][test.__name__].extend(durations)

    async def _gather_calls(self, test: BenchmarkTest, call_count: int) -> list[int]:
        return list(await asyncio.gather(*(self._timed_call(test) for _ in range(call_count))))

    @staticmethod
    async def _timed_call(test: BenchmarkTest) -> int:
        start = time.perf_counter()
        await asyncio.to_thread(test)
        return round((time.perf_counter() - start) * 1000)

    @staticmethod
    def _percentile(durations: list[int], percentile: float) -> int:
        if not durations:
            return 0
        sorted_durations = sorted(durations)
        index = int(round((percentile / 100) * (len(sorted_durations) - 1)))
        return sorted_durations[index]
