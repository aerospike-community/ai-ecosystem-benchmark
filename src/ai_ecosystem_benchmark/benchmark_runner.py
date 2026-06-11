"""Benchmark runner."""

import math
import time
import warnings
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Barrier, BrokenBarrierError, Lock

from ai_ecosystem_benchmark._latency_histogram import LatencyHistogram
from ai_ecosystem_benchmark.base_benchmark_workload import BaseBenchmarkWorkload, BenchmarkTest

_BACKENDS = ("aerospike", "postgres", "redis")
_NS_PER_SECOND = 1_000_000_000
# Sleep coarsely for most of the wait, then busy-wait the final slice for pacing precision.
_COARSE_SLEEP_FLOOR_NS = 1_500_000  # 1.5 ms
# Absolute floor for dispatch-lag warnings.
_CONGESTION_DISPATCH_LAG_FLOOR_NS = 50_000_000  # 50 ms
# Treat the worker pool as saturated when peak active calls are near the pool size.
_CONGESTION_SATURATION_RATIO = 0.9
# Ignore isolated dispatch-lag blips.
_CONGESTION_MIN_LAG_FRACTION = 0.01  # 1% of calls
# Minimum laggy dispatches required before any warning fires.
_CONGESTION_MIN_LAG_COUNT = 5
# Required fraction of target qps for the run to be considered sustainable.
_SUSTAINED_THROUGHPUT_RATIO = 0.9

# --- Worker-pool sizing -------------------------------------------------------------------
# Little's Law headroom over the warmup probe. The probe runs at low concurrency, so
# load-time latency runs higher; this margin absorbs that growth for moderate ops.
_WORKER_HEADROOM = 4.0
# Minimum auto-sized pool, clamped by ``worker_thread_count``.
_MIN_WORKER_THREADS = 32
# Warmup probe settings; probe calls are not recorded in benchmark metrics.
_CALIBRATION_CALLS = 64
_CALIBRATION_CONCURRENCY = 16
# Fallback pool size when all probe calls fail.
_CALIBRATION_FALLBACK_WORKERS = 256

# --- Worker-pool sharding -----------------------------------------------------------------
# Bound each executor's worker count to avoid one large shared queue becoming a dispatch
# bottleneck. Larger pools are split across multiple executors.
_WORKERS_PER_SHARD = 64


class BenchmarkRunner:
    """Coordinates benchmark execution for a workload.

    Calls are scheduled at a fixed rate and measured from their scheduled start time, so
    client-side queueing is visible in the latency metrics. Each test runs a mandatory warmup
    to size and pre-warm the worker pool before the timed run.

    Large worker pools are sharded across multiple executors to avoid a single shared work
    queue becoming the client bottleneck. ``worker_thread_count`` is the total pool cap.
    """

    def __init__(
        self,
        queries_per_second: int,
        scheduler_thread_count: int,
        worker_thread_count: int,
        runtime_per_function: int,
        workload: BaseBenchmarkWorkload,
    ) -> None:
        if queries_per_second <= 0:
            raise ValueError("queries_per_second must be positive")
        if scheduler_thread_count <= 0:
            raise ValueError("scheduler_thread_count must be positive")
        if worker_thread_count <= 0:
            raise ValueError("worker_thread_count must be positive")
        if runtime_per_function <= 0:
            raise ValueError("runtime_per_function must be positive")

        self.queries_per_second = queries_per_second
        self.scheduler_thread_count = scheduler_thread_count
        # Maximum total workers after warmup sizing.
        self.worker_thread_count = worker_thread_count
        self.runtime_per_function = runtime_per_function
        self.workload = workload
        # Response latency, measured from a call's scheduled start; includes client queue wait.
        self.metrics: dict[str, dict[str, LatencyHistogram]] = defaultdict(
            lambda: defaultdict(LatencyHistogram)
        )
        # Service latency, measured from worker pickup; the backend's true per-call cost.
        self.service_metrics: dict[str, dict[str, LatencyHistogram]] = defaultdict(
            lambda: defaultdict(LatencyHistogram)
        )
        self.failures: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

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
        print(
            "  response = scheduled-time latency (incl. client queue wait); "
            "service = execution-only latency"
        )
        for backend in _BACKENDS:
            print(f"\n[{backend}]")
            tests = self.metrics.get(backend, {})
            service_tests = self.service_metrics.get(backend, {})
            failures_for_backend = self.failures.get(backend, {})
            if not tests and not failures_for_backend:
                print("  (no tests run)")
                continue
            # Include tests that only failed.
            test_names = sorted(set(tests) | set(failures_for_backend))
            for test_name in test_names:
                histogram = tests.get(test_name)
                service_histogram = service_tests.get(test_name)
                count = histogram.count() if histogram else 0
                failure_count = failures_for_backend.get(test_name, 0)
                print(f"  {test_name}: calls={count} failures={failure_count}")
                print(f"      response {_format_percentiles(histogram)}")
                print(f"      service  {_format_percentiles(service_histogram)}")

    def _run_test(self, backend: str, test: BenchmarkTest) -> None:
        total_calls = self.queries_per_second * self.runtime_per_function
        print(
            f"Running {backend}.{test.__name__} with {total_calls} calls "
            f"({self.queries_per_second} qps for {self.runtime_per_function}s)"
        )
        histogram = self.metrics[backend][test.__name__]
        service_histogram = self.service_metrics[backend][test.__name__]
        failure_count = self._run_scheduled(
            backend, test, total_calls, histogram, service_histogram
        )
        if failure_count:
            self.failures[backend][test.__name__] += failure_count

    def _run_scheduled(
        self,
        backend: str,
        test: BenchmarkTest,
        total_calls: int,
        histogram: LatencyHistogram,
        service_histogram: LatencyHistogram,
    ) -> int:
        if total_calls <= 0:
            return 0

        pool_size = self._size_worker_pool(backend, test, total_calls)

        scheduler_count = self.scheduler_thread_count
        # Split the target rate evenly across scheduler threads.
        global_interval_ns = _NS_PER_SECOND // self.queries_per_second
        per_thread_interval_ns = scheduler_count * global_interval_ns
        base_calls_per_thread = total_calls // scheduler_count
        extra_calls = total_calls % scheduler_count

        failure_count = 0
        failure_lock = Lock()
        first_failure_warned = False
        # Dispatch lag isolates client queue wait from test execution time.
        congestion_threshold_ns = max(_CONGESTION_DISPATCH_LAG_FLOOR_NS, per_thread_interval_ns)
        # Aggregate once after the run so isolated jitter does not warn.
        stats_lock = Lock()
        in_flight = 0
        peak_in_flight = 0
        lag_count = 0
        worst_lag_ns = 0

        def execute(scheduled_start_ns: int) -> None:
            nonlocal failure_count, first_failure_warned
            nonlocal in_flight, peak_in_flight, lag_count, worst_lag_ns
            dispatch_ns = time.perf_counter_ns()
            dispatch_lag_ns = dispatch_ns - scheduled_start_ns
            with stats_lock:
                in_flight += 1
                if in_flight > peak_in_flight:
                    peak_in_flight = in_flight
                if dispatch_lag_ns > congestion_threshold_ns:
                    lag_count += 1
                    if dispatch_lag_ns > worst_lag_ns:
                        worst_lag_ns = dispatch_lag_ns
            try:
                test()
            except Exception as exc:
                with failure_lock:
                    failure_count += 1
                    should_warn = not first_failure_warned
                    if should_warn:
                        first_failure_warned = True
                if should_warn:
                    warnings.warn(
                        f"{backend}.{test.__name__}: first failure: {type(exc).__name__}: {exc}",
                        stacklevel=2,
                    )
            else:
                end_ns = time.perf_counter_ns()
                # Response latency includes queue wait; service latency is execution only.
                histogram.record(end_ns - scheduled_start_ns)
                service_histogram.record(end_ns - dispatch_ns)
            finally:
                with stats_lock:
                    in_flight -= 1

        shard_sizes = self._shard_layout(pool_size)
        num_shards = len(shard_sizes)
        with ExitStack() as stack:
            # Keep each executor small enough that its queue lock is not the bottleneck.
            worker_shards = [
                stack.enter_context(ThreadPoolExecutor(max_workers=size)) for size in shard_sizes
            ]
            scheduler_pool = stack.enter_context(ThreadPoolExecutor(max_workers=scheduler_count))

            # Materialize all worker threads before the timed run.
            try:
                for shard, size in zip(worker_shards, shard_sizes, strict=True):
                    _prewarm_pool(shard, size)
            except (MemoryError, RuntimeError) as exc:
                warnings.warn(
                    f"{backend}.{test.__name__}: could not create {pool_size} worker threads "
                    f"({type(exc).__name__}: {exc}); client thread or memory limit reached. "
                    f"Lower worker_thread_count or use a larger client.",
                    stacklevel=2,
                )
                return 0

            # Put initial deadlines slightly in the future after thread startup.
            origin_ns = time.perf_counter_ns() + 10_000_000  # 10 ms

            def schedule_thread_load(thread_index: int) -> None:
                # Stagger scheduler threads by one global slot.
                thread_origin_ns = origin_ns + thread_index * global_interval_ns
                calls_for_thread = base_calls_per_thread + (1 if thread_index < extra_calls else 0)
                # Spread submits across worker-shard queues.
                shard_cursor = thread_index % num_shards
                for slot_index in range(calls_for_thread):
                    target_ns = thread_origin_ns + slot_index * per_thread_interval_ns
                    _sleep_until_ns(target_ns)
                    worker_shards[shard_cursor].submit(execute, target_ns)
                    shard_cursor = (shard_cursor + 1) % num_shards

            scheduler_futures = [
                scheduler_pool.submit(schedule_thread_load, i) for i in range(scheduler_count)
            ]
            for future in scheduler_futures:
                future.result()
        # Includes drain time after scheduling, so backlog reduces achieved qps.
        drain_complete_ns = time.perf_counter_ns()
        elapsed_s = (drain_complete_ns - origin_ns) / _NS_PER_SECOND
        achieved_qps = total_calls / elapsed_s if elapsed_s > 0 else float(self.queries_per_second)

        self._maybe_warn_congestion(
            backend=backend,
            test=test,
            total_calls=total_calls,
            congestion_threshold_ns=congestion_threshold_ns,
            pool_size=pool_size,
            achieved_qps=achieved_qps,
            peak_in_flight=peak_in_flight,
            lag_count=lag_count,
            worst_lag_ns=worst_lag_ns,
        )
        return failure_count

    def _size_worker_pool(self, backend: str, test: BenchmarkTest, total_calls: int) -> int:
        """Choose the total worker count to pre-warm for ``test``."""
        cap = self.worker_thread_count
        probe_calls = min(_CALIBRATION_CALLS, total_calls)
        p95_latency_ns = self._probe_latency_ns(test, probe_calls) if probe_calls > 0 else None
        if p95_latency_ns is None:
            pool_size = self._clamp_pool_size(_CALIBRATION_FALLBACK_WORKERS)
            print(
                f"  warmup: {backend}.{test.__name__}: probe inconclusive; "
                f"pre-warming {pool_size} workers (cap {cap})"
            )
            return pool_size

        raw_estimate = math.ceil(
            self.queries_per_second * (p95_latency_ns / _NS_PER_SECOND) * _WORKER_HEADROOM
        )
        pool_size = self._clamp_pool_size(raw_estimate)
        note = ""
        if raw_estimate > cap:
            note = (
                f"; estimate {raw_estimate} exceeds cap -- action: raise worker_thread_count "
                f"or lower qps"
            )
        print(
            f"  warmup: {backend}.{test.__name__}: p95 latency "
            f"~{p95_latency_ns / 1_000_000:.1f} ms -> pre-warming {pool_size} workers "
            f"(cap {cap}{note})"
        )
        return pool_size

    def _clamp_pool_size(self, value: int) -> int:
        """Clamp a sizing ``value`` to ``[min(_MIN_WORKER_THREADS, cap), cap]``."""
        cap = self.worker_thread_count
        return max(min(value, cap), min(_MIN_WORKER_THREADS, cap))

    def _shard_layout(self, pool_size: int) -> list[int]:
        """Return worker counts for executor shards, summing to ``pool_size``."""
        if pool_size <= 0:
            return []
        num_shards = max(1, math.ceil(pool_size / _WORKERS_PER_SHARD))
        base, extra = divmod(pool_size, num_shards)
        return [base + (1 if i < extra else 0) for i in range(num_shards)]

    def _probe_latency_ns(self, test: BenchmarkTest, probe_calls: int) -> int | None:
        """Run the warmup latency probe and return p95 latency in ns."""
        latencies_ns: list[int] = []
        latencies_lock = Lock()

        def probe() -> None:
            start_ns = time.perf_counter_ns()
            try:
                test()
            except Exception:
                return
            elapsed_ns = time.perf_counter_ns() - start_ns
            with latencies_lock:
                latencies_ns.append(elapsed_ns)

        probe_concurrency = min(self.worker_thread_count, _CALIBRATION_CONCURRENCY, probe_calls)
        with ThreadPoolExecutor(max_workers=probe_concurrency) as probe_pool:
            for future in [probe_pool.submit(probe) for _ in range(probe_calls)]:
                future.result()

        if not latencies_ns:
            return None
        latencies_ns.sort()
        index = min(len(latencies_ns) - 1, int(len(latencies_ns) * 0.95))
        return latencies_ns[index]

    def _maybe_warn_congestion(
        self,
        *,
        backend: str,
        test: BenchmarkTest,
        total_calls: int,
        congestion_threshold_ns: int,
        pool_size: int,
        achieved_qps: float,
        peak_in_flight: int,
        lag_count: int,
        worst_lag_ns: int,
    ) -> None:
        """Emit one concise warning for sustained client-side queueing."""
        if lag_count <= 0:
            return
        # Ignore isolated lag samples.
        min_sustained = max(_CONGESTION_MIN_LAG_COUNT, total_calls * _CONGESTION_MIN_LAG_FRACTION)
        if lag_count < min_sustained:
            return

        worst_lag_ms = worst_lag_ns / 1_000_000
        threshold_ms = congestion_threshold_ns / 1_000_000
        target_qps = self.queries_per_second
        kept_up = achieved_qps >= target_qps * _SUSTAINED_THROUGHPUT_RATIO
        saturated = peak_in_flight >= pool_size * _CONGESTION_SATURATION_RATIO
        at_cap = pool_size >= self.worker_thread_count
        rate = f"achieved ~{achieved_qps:.0f} of {target_qps} target qps"

        if not kept_up and not saturated:
            warnings.warn(
                f"{backend}.{test.__name__}: {rate}; worker pool idle "
                f"(peak {peak_in_flight}/{pool_size}), worst dispatch lag "
                f"{worst_lag_ms:.1f} ms. The backend or DB connection pool cannot sustain "
                f"the target rate.",
                stacklevel=2,
            )
            return

        if saturated and at_cap:
            warnings.warn(
                f"{backend}.{test.__name__}: worker pool hit the {pool_size}-thread cap; "
                f"{rate}; worst dispatch lag {worst_lag_ms:.1f} ms. The client worker cap is "
                f"the bottleneck. Raise worker_thread_count or use a larger client.",
                stacklevel=2,
            )
            return

        if saturated:
            if not kept_up:
                warnings.warn(
                    f"{backend}.{test.__name__}: worker pool saturated ({pool_size}); "
                    f"{rate}; worst dispatch lag {worst_lag_ms:.1f} ms. Per-call latency grew "
                    f"under load: the backend or DB connection pool is the bottleneck.",
                    stacklevel=2,
                )
                return
            warnings.warn(
                f"{backend}.{test.__name__}: worker pool saturated below cap ({pool_size}); "
                f"{rate}; worst dispatch lag {worst_lag_ms:.1f} ms. The warmup probe "
                f"under-sized the pool for load-time latency.",
                stacklevel=2,
            )
            return

        warnings.warn(
            f"{backend}.{test.__name__}: target qps met ({rate}) but sustained dispatch lag "
            f"(worst {worst_lag_ms:.1f} ms > {threshold_ms:.1f} ms). Likely host scheduler "
            f"jitter or GC pauses.",
            stacklevel=2,
        )


def _prewarm_pool(pool: ThreadPoolExecutor, count: int) -> None:
    """Create ``count`` worker threads before timed work starts."""
    if count <= 0:
        return
    barrier = Barrier(count + 1)

    def _hold() -> None:
        try:
            barrier.wait()
        except BrokenBarrierError:
            return

    try:
        for _ in range(count):
            pool.submit(_hold)
    except (RuntimeError, MemoryError):
        barrier.abort()
        raise
    barrier.wait()


def _ns_to_ms(value_ns: int) -> int:
    return round(value_ns / 1_000_000)


def _format_percentiles(histogram: LatencyHistogram | None) -> str:
    """Format p50/p90/p99 (ms) for a histogram, or zeros when it is missing/empty."""
    if histogram is None:
        return "p50=0ms  p90=0ms  p99=0ms"
    p50_ms = _ns_to_ms(histogram.percentile_ns(50))
    p90_ms = _ns_to_ms(histogram.percentile_ns(90))
    p99_ms = _ns_to_ms(histogram.percentile_ns(99))
    return f"p50={p50_ms}ms  p90={p90_ms}ms  p99={p99_ms}ms"


def _sleep_until_ns(target_ns: int) -> None:
    """Block until ``time.perf_counter_ns()`` reaches ``target_ns``."""
    while True:
        remaining_ns = target_ns - time.perf_counter_ns()
        if remaining_ns <= 0:
            return
        if remaining_ns > _COARSE_SLEEP_FLOOR_NS:
            time.sleep((remaining_ns - _COARSE_SLEEP_FLOOR_NS) / _NS_PER_SECOND)
