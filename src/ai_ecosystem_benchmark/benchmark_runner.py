"""Benchmark runner."""

from ai_ecosystem_benchmark.base_benchmark_workload import BaseBenchmarkWorkload


class BenchmarkRunner:
    """Coordinates benchmark execution settings for a workload."""

    def __init__(
        self,
        thread_count: int,
        queries_per_second: int,
        workload_class: type[BaseBenchmarkWorkload],
    ) -> None:
        self.thread_count = thread_count
        self.queries_per_second = queries_per_second
        self.workload_class = workload_class
