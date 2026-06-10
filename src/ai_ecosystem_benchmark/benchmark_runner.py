"""Benchmark runner."""

import csv
import math
import os
import time
import warnings
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock, Thread

from ai_ecosystem_benchmark._latency_histogram import LatencyHistogram
from ai_ecosystem_benchmark.base_benchmark_workload import BaseBenchmarkWorkload, BenchmarkTest

_BACKENDS = ("aerospike", "postgres", "redis")
_NS_PER_SECOND = 1_000_000_000
_DEFAULT_CSV_PATH = "benchmark_metrics_windows.csv"
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
# When the remaining wait is above this threshold, fall back to ``time.sleep`` (coarse, ~ms
# precision) for most of the wait. We always finish the last slice with a busy-wait so we
# can hit the target deadline with nanosecond precision regardless of OS scheduler jitter.
_COARSE_SLEEP_FLOOR_NS = 1_500_000  # 1.5 ms
# Absolute floor for the congestion threshold. Submit + worker wake-up is microseconds
# in steady state, so 50 ms is well above the noise floor for any qps. At low qps where
# the per-thread interval already exceeds this, the relative term dominates instead.
_CONGESTION_DISPATCH_LAG_FLOOR_NS = 50_000_000  # 50 ms


class BenchmarkRunner:
    """Coordinates benchmark execution for a workload.

    The runner schedules calls at a fixed ``queries_per_second`` rate using a dedicated
    scheduler thread pool with nanosecond-precision pacing. Each scheduled call is handed
    off to a worker thread pool that actually executes the workload method. Latencies are
    measured from the *scheduled* start time (not the actual dispatch time), so any
    queueing delay caused by saturated workers is reflected in the metrics. This is the
    standard correction for coordinated omission.
    """

    def __init__(
        self,
        queries_per_second: int,
        scheduler_thread_count: int,
        worker_thread_count: int,
        runtime_per_function: int,
        workload: BaseBenchmarkWorkload,
        metrics_window_seconds: int | None = None,
        csv_output_path: str | None = None,
    ) -> None:
        if queries_per_second <= 0:
            raise ValueError("queries_per_second must be positive")
        if scheduler_thread_count <= 0:
            raise ValueError("scheduler_thread_count must be positive")
        if worker_thread_count <= 0:
            raise ValueError("worker_thread_count must be positive")
        if runtime_per_function <= 0:
            raise ValueError("runtime_per_function must be positive")
        if metrics_window_seconds is not None and metrics_window_seconds <= 0:
            raise ValueError("metrics_window_seconds must be positive")

        self.queries_per_second = queries_per_second
        self.scheduler_thread_count = scheduler_thread_count
        self.worker_thread_count = worker_thread_count
        self.runtime_per_function = runtime_per_function
        self.workload = workload
        self.metrics_window_seconds = metrics_window_seconds
        if metrics_window_seconds is not None:
            self.csv_output_path = csv_output_path or _DEFAULT_CSV_PATH
        else:
            self.csv_output_path = None
        # Per-test latency histograms (constant memory regardless of call volume).
        # Only *successful* calls are recorded; failed calls are tracked separately.
        self.metrics: dict[str, dict[str, LatencyHistogram]] = defaultdict(
            lambda: defaultdict(LatencyHistogram)
        )
        # Number of calls that raised an exception, keyed by backend and test name.
        self.failures: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._csv_lock = Lock()

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
            failures_for_backend = self.failures.get(backend, {})
            if not tests and not failures_for_backend:
                print("  (no tests run)")
                continue
            # Iterate the union of successes and failures so a test that failed every
            # call still shows up with a failure count.
            test_names = sorted(set(tests) | set(failures_for_backend))
            for test_name in test_names:
                histogram = tests.get(test_name)
                count = histogram.count() if histogram else 0
                failure_count = failures_for_backend.get(test_name, 0)
                p50_ms = _ns_to_ms(histogram.percentile_ns(50)) if histogram else 0
                p90_ms = _ns_to_ms(histogram.percentile_ns(90)) if histogram else 0
                p99_ms = _ns_to_ms(histogram.percentile_ns(99)) if histogram else 0
                print(
                    f"  {test_name}: calls={count} failures={failure_count}  "
                    f"p50={p50_ms}ms  p90={p90_ms}ms  p99={p99_ms}ms"
                )

    def _run_test(self, backend: str, test: BenchmarkTest) -> None:
        total_calls = self.queries_per_second * self.runtime_per_function
        print(
            f"Running {backend}.{test.__name__} with {total_calls} calls "
            f"({self.queries_per_second} qps for {self.runtime_per_function}s)"
        )
        histogram = self.metrics[backend][test.__name__]
        failure_count = self._run_scheduled(backend, test, total_calls, histogram)
        if failure_count:
            self.failures[backend][test.__name__] += failure_count

    def _run_scheduled(
        self,
        backend: str,
        test: BenchmarkTest,
        total_calls: int,
        histogram: LatencyHistogram,
    ) -> int:
        if total_calls <= 0:
            return 0

        scheduler_count = self.scheduler_thread_count
        # ``queries_per_second`` is the *total* per-second load across the whole runner.
        # We split it evenly across scheduler threads: each thread paces independently at
        # ``queries_per_second / scheduler_thread_count`` qps, and the staggered start
        # offsets below keep the global rate at exactly ``queries_per_second``.
        global_interval_ns = _NS_PER_SECOND // self.queries_per_second
        per_thread_interval_ns = scheduler_count * global_interval_ns
        # Distribute ``total_calls`` as evenly as possible across scheduler threads.
        base_calls_per_thread = total_calls // scheduler_count
        extra_calls = total_calls % scheduler_count

        failure_count = 0
        failure_lock = Lock()
        first_failure_warned = False
        # Congestion detection: ``dispatch_lag = pickup_time - scheduled_start_time``
        # isolates worker-queue wait from test execution. The threshold is the larger of
        # an absolute noise floor and ``per_thread_interval_ns``: dispatch lag at or
        # above the per-thread interval means the worker queue has accumulated at least
        # one call per scheduler thread, which is the Little's-Law tipping point for the
        # queue growing rather than draining.
        congestion_threshold_ns = max(_CONGESTION_DISPATCH_LAG_FLOOR_NS, per_thread_interval_ns)
        # Plain bool + lock with double-checked locking lets us emit a single warning
        # per test even when many workers cross the threshold simultaneously.
        congestion_warned = False
        congestion_lock = Lock()

        windowing_enabled = self.metrics_window_seconds is not None
        window_seconds = self.metrics_window_seconds or 0
        window_ns = window_seconds * _NS_PER_SECOND
        num_windows = (
            math.ceil(self.runtime_per_function / window_seconds) if windowing_enabled else 0
        )
        window_histograms: list[LatencyHistogram] = []
        window_successes: list[int] = []
        window_failures: list[int] = []
        window_counters_lock = Lock()
        sealed_windows: set[int] = set()
        sealed_lock = Lock()
        seal_done_event = Event()
        seal_thread: Thread | None = None
        origin_ns_ref: list[int] = [0]

        if windowing_enabled:
            window_histograms = [LatencyHistogram() for _ in range(num_windows)]
            window_successes = [0] * num_windows
            window_failures = [0] * num_windows
            seal_thread = Thread(
                target=_window_seal_thread,
                args=(
                    self,
                    backend,
                    test.__name__,
                    origin_ns_ref,
                    window_seconds,
                    window_ns,
                    num_windows,
                    window_histograms,
                    window_successes,
                    window_failures,
                    window_counters_lock,
                    sealed_windows,
                    sealed_lock,
                    seal_done_event,
                ),
                daemon=True,
            )

        origin_ns = 0

        def record_failure(end_ns: int) -> None:
            if windowing_enabled:
                window_idx = _window_index(end_ns - origin_ns, window_ns, num_windows)
                with window_counters_lock:
                    window_failures[window_idx] += 1

        def record_success(end_ns: int, scheduled_start_ns: int) -> None:
            latency_ns = end_ns - scheduled_start_ns
            histogram.record(latency_ns)
            if windowing_enabled:
                window_idx = _window_index(end_ns - origin_ns, window_ns, num_windows)
                window_histograms[window_idx].record(latency_ns)
                with window_counters_lock:
                    window_successes[window_idx] += 1

        def execute(scheduled_start_ns: int) -> None:
            nonlocal congestion_warned, failure_count, first_failure_warned
            dispatch_ns = time.perf_counter_ns()
            dispatch_lag_ns = dispatch_ns - scheduled_start_ns
            if dispatch_lag_ns > congestion_threshold_ns and not congestion_warned:
                with congestion_lock:
                    if not congestion_warned:
                        congestion_warned = True
                        warnings.warn(
                            f"{backend}.{test.__name__}: worker pool congestion detected "
                            f"(dispatch lag {dispatch_lag_ns / 1_000_000:.1f} ms > "
                            f"{congestion_threshold_ns / 1_000_000:.1f} ms threshold). "
                            f"Increase worker_thread_count "
                            f"(currently {self.worker_thread_count}) to avoid queueing "
                            f"delays polluting latency measurements.",
                            stacklevel=2,
                        )
            try:
                test()
            except Exception as exc:
                end_ns = time.perf_counter_ns()
                with failure_lock:
                    failure_count += 1
                    should_warn = not first_failure_warned
                    if should_warn:
                        first_failure_warned = True
                record_failure(end_ns)
                if should_warn:
                    warnings.warn(
                        f"{backend}.{test.__name__}: first failure: {type(exc).__name__}: {exc}",
                        stacklevel=2,
                    )
            else:
                end_ns = time.perf_counter_ns()
                record_success(end_ns, scheduled_start_ns)

        with (
            ThreadPoolExecutor(max_workers=self.worker_thread_count) as worker_pool,
            ThreadPoolExecutor(max_workers=scheduler_count) as scheduler_pool,
        ):
            # Give every scheduler thread a small head start so the first batch of
            # deadlines is genuinely in the future even after thread start-up cost.
            origin_ns = time.perf_counter_ns() + 10_000_000  # 10 ms
            origin_ns_ref[0] = origin_ns
            if seal_thread is not None:
                seal_thread.start()

            def schedule_thread_load(thread_index: int) -> None:
                # Stagger threads by one global slot so their combined firing pattern
                # matches the requested ``queries_per_second`` rate exactly.
                thread_origin_ns = origin_ns + thread_index * global_interval_ns
                calls_for_thread = base_calls_per_thread + (1 if thread_index < extra_calls else 0)
                for slot_index in range(calls_for_thread):
                    target_ns = thread_origin_ns + slot_index * per_thread_interval_ns
                    _sleep_until_ns(target_ns)
                    worker_pool.submit(execute, target_ns)

            scheduler_futures = [
                scheduler_pool.submit(schedule_thread_load, i) for i in range(scheduler_count)
            ]
            for future in scheduler_futures:
                future.result()

        if seal_thread is not None:
            seal_done_event.set()
            seal_thread.join()

        return failure_count

    def _append_csv_row(
        self,
        backend: str,
        test_name: str,
        window_idx: int,
        window_seconds: int,
        calls: int,
        failures: int,
        histogram: LatencyHistogram,
    ) -> None:
        if self.csv_output_path is None:
            return
        window_start_sec = window_idx * window_seconds
        total_calls = calls + failures
        calls_per_sec = round(total_calls / window_seconds)
        if calls > 0:
            p50_ms = _ns_to_ms(histogram.percentile_ns(50))
            p90_ms = _ns_to_ms(histogram.percentile_ns(90))
            p99_ms = _ns_to_ms(histogram.percentile_ns(99))
        else:
            p50_ms = 0
            p90_ms = 0
            p99_ms = 0
        row = [
            backend,
            test_name,
            window_start_sec,
            window_seconds,
            calls,
            failures,
            calls_per_sec,
            p50_ms,
            p90_ms,
            p99_ms,
        ]
        with self._csv_lock:
            write_header = (
                not os.path.exists(self.csv_output_path)
                or os.path.getsize(self.csv_output_path) == 0
            )
            with open(self.csv_output_path, "a", newline="") as csv_file:
                writer = csv.writer(csv_file)
                if write_header:
                    writer.writerow(_CSV_HEADER)
                writer.writerow(row)


def _window_index(elapsed_ns: int, window_ns: int, num_windows: int) -> int:
    idx = elapsed_ns // window_ns
    return max(0, min(idx, num_windows - 1))


def _window_seal_thread(
    runner: BenchmarkRunner,
    backend: str,
    test_name: str,
    origin_ns_ref: list[int],
    window_seconds: int,
    window_ns: int,
    num_windows: int,
    window_histograms: list[LatencyHistogram],
    window_successes: list[int],
    window_failures: list[int],
    counters_lock: Lock,
    sealed_windows: set[int],
    sealed_lock: Lock,
    done_event: Event,
) -> None:
    def seal_one(window_idx: int) -> None:
        with sealed_lock:
            if window_idx in sealed_windows:
                return
            sealed_windows.add(window_idx)
        with counters_lock:
            calls = window_successes[window_idx]
            failures = window_failures[window_idx]
        runner._append_csv_row(
            backend,
            test_name,
            window_idx,
            window_seconds,
            calls,
            failures,
            window_histograms[window_idx],
        )

    origin_ns = origin_ns_ref[0]
    for window_idx in range(num_windows - 1):
        _sleep_until_ns(origin_ns + (window_idx + 1) * window_ns)
        seal_one(window_idx)

    done_event.wait()

    for window_idx in range(num_windows):
        seal_one(window_idx)


def _ns_to_ms(value_ns: int) -> int:
    return round(value_ns / 1_000_000)


def _sleep_until_ns(target_ns: int) -> None:
    """Block until ``time.perf_counter_ns()`` reaches ``target_ns``.

    Uses ``time.sleep`` for the bulk of the wait and a tight busy-wait for the final
    slice to compensate for the OS scheduler's ~1 ms granularity.
    """
    while True:
        remaining_ns = target_ns - time.perf_counter_ns()
        if remaining_ns <= 0:
            return
        if remaining_ns > _COARSE_SLEEP_FLOOR_NS:
            time.sleep((remaining_ns - _COARSE_SLEEP_FLOOR_NS) / _NS_PER_SECOND)
