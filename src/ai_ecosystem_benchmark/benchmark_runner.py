"""Benchmark runner."""

import math
import time
import warnings
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, BrokenBarrierError, Lock

from ai_ecosystem_benchmark._latency_histogram import LatencyHistogram
from ai_ecosystem_benchmark.base_benchmark_workload import BaseBenchmarkWorkload, BenchmarkTest

_BACKENDS = ("aerospike", "postgres", "redis")
_NS_PER_SECOND = 1_000_000_000
# When the remaining wait is above this threshold, fall back to ``time.sleep`` (coarse, ~ms
# precision) for most of the wait. We always finish the last slice with a busy-wait so we
# can hit the target deadline with nanosecond precision regardless of OS scheduler jitter.
_COARSE_SLEEP_FLOOR_NS = 1_500_000  # 1.5 ms
# Absolute floor for the congestion threshold. Submit + worker wake-up is microseconds
# in steady state, so 50 ms is well above the noise floor for any qps. At low qps where
# the per-thread interval already exceeds this, the relative term dominates instead.
_CONGESTION_DISPATCH_LAG_FLOOR_NS = 50_000_000  # 50 ms
# A run is treated as worker-pool saturated when peak in-flight concurrency reaches at least
# this fraction of the pool size. Peak in-flight can never exceed the pool (a call only counts
# as in-flight once a worker picks it up), so reaching it means calls genuinely queued for a
# worker -- the only case where more workers help.
_CONGESTION_SATURATION_RATIO = 0.9
# Dispatch lag without saturation comes from something other than a worker shortage (GC
# pauses, OS scheduler jitter, backend latency spikes). A single such blip is noise and must
# not warn; we only flag it when it affects at least this fraction of all calls, i.e. it is
# sustained enough to actually skew the latency distribution.
_CONGESTION_MIN_LAG_FRACTION = 0.01  # 1% of calls
# An absolute floor on laggy dispatches required before any warning fires, so tiny runs
# can't trip on one or two samples even when the fraction looks large.
_CONGESTION_MIN_LAG_COUNT = 5
# A test is "keeping up" only if its achieved throughput is at least this fraction of the
# target qps. Below it, calls are queueing faster than they drain and the recorded latencies
# are dominated by backlog (coordinated omission) rather than real service time.
_SUSTAINED_THROUGHPUT_RATIO = 0.9

# --- Worker-pool sizing -------------------------------------------------------------------
# A short latency probe (the always-on warmup) estimates per-call latency; Little's Law
# (concurrency = qps x latency) times this headroom gives the pool size, absorbing latency
# variance and brief bursts so the timed run never has to grow the pool.
_WORKER_HEADROOM = 3.0
# Never size the pool below this (clamped to ``worker_thread_count``). Keeps fast,
# sub-millisecond ops from being throttled by a tiny pool. It also keeps fast-op pools small
# enough that hundreds of idle workers don't pile up on the executor's single work-queue lock.
_MIN_WORKER_THREADS = 32
# Latency-probe settings. A short, low-concurrency burst estimates per-call latency without
# itself saturating anything; probe calls are never recorded in the metrics.
_CALIBRATION_CALLS = 64
_CALIBRATION_CONCURRENCY = 16
# If every probe call fails (so latency can't be estimated), fall back to this many workers
# (clamped to the cap) rather than the configured cap, which may be large.
_CALIBRATION_FALLBACK_WORKERS = 256


class BenchmarkRunner:
    """Coordinates benchmark execution for a workload.

    The runner schedules calls at a fixed ``queries_per_second`` rate using a dedicated
    scheduler thread pool with nanosecond-precision pacing. Each scheduled call is handed
    off to a worker thread pool that actually executes the workload method. Latencies are
    measured from the *scheduled* start time (not the actual dispatch time), so any
    queueing delay caused by saturated workers is reflected in the metrics. This is the
    standard correction for coordinated omission.

    To keep the client from polluting its own measurements, every test runs a mandatory
    warmup: a short latency probe sizes the worker pool from Little's Law, and the pool is
    then **fully pre-warmed** (every worker thread materialized up front, ``max_workers``
    pinned to that size) so the timed run never pays OS thread-creation cost -- which
    otherwise serializes inside ``ThreadPoolExecutor.submit`` and shows up as dispatch lag.

    The pool size is capped at ``worker_thread_count`` (the single configured maximum, also
    the most threads ever pre-warmed). ``submit`` spawns a thread per backed-up call, so an
    uncapped pool would balloon under a slow backend; the cap bounds both that growth and the
    eager pre-warm. When an op needs more concurrency than the cap allows, that is surfaced as
    a client-side limit (and a too-large pre-warm fails gracefully) rather than crashing the
    run or silently distorting the numbers.
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
        # The single worker-pool knob: the maximum pool size *and* the most threads ever
        # pre-warmed. Each test's pool is sized from a latency probe and clamped to this; the
        # whole sized pool is pre-warmed, so this also bounds start-up memory/thread usage.
        self.worker_thread_count = worker_thread_count
        self.runtime_per_function = runtime_per_function
        self.workload = workload
        # Per-test latency histograms (constant memory regardless of call volume).
        # Only *successful* calls are recorded; failed calls are tracked separately.
        self.metrics: dict[str, dict[str, LatencyHistogram]] = defaultdict(
            lambda: defaultdict(LatencyHistogram)
        )
        # Number of calls that raised an exception, keyed by backend and test name.
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

        pool_size = self._size_worker_pool(backend, test, total_calls)

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
        # Aggregate congestion stats, evaluated once after the run (not per-sample) so a
        # lone jittery pickup can't trip a warning. ``peak_in_flight`` distinguishes a pool
        # that genuinely saturated (queueing is real) from lag that more workers can't fix
        # (downstream/backend/jitter). All guarded by ``stats_lock``.
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
                histogram.record(end_ns - scheduled_start_ns)
            finally:
                with stats_lock:
                    in_flight -= 1

        with (
            ThreadPoolExecutor(max_workers=pool_size) as worker_pool,
            ThreadPoolExecutor(max_workers=scheduler_count) as scheduler_pool,
        ):
            # Materialize the *entire* pool before timing so the timed run never creates a
            # thread on the hot path (see class docstring). ``max_workers == pool_size`` and
            # every one is warmed here, so there is no lazy growth left to pollute latencies.
            # If the client can't create that many threads, fail this test gracefully (with an
            # actionable message) instead of crashing the whole suite with a bare error.
            try:
                _prewarm_pool(worker_pool, pool_size)
            except (MemoryError, RuntimeError) as exc:
                warnings.warn(
                    f"{backend}.{test.__name__}: could not pre-warm {pool_size} worker threads "
                    f"({type(exc).__name__}: {exc}). The client hit an OS thread/memory limit "
                    f"before the pool was ready -- this is a client-hardware limit, not a "
                    f"backend result. Lower worker_thread_count (currently "
                    f"{self.worker_thread_count}) or qps, or provision a larger client. "
                    f"Skipping this test.",
                    stacklevel=2,
                )
                return 0

            # Give every scheduler thread a small head start so the first batch of
            # deadlines is genuinely in the future even after thread start-up cost.
            origin_ns = time.perf_counter_ns() + 10_000_000  # 10 ms

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
        # The ``with`` block above exits only after both pools shut down with ``wait=True``,
        # i.e. once every submitted call has actually completed. Measuring from the first
        # scheduled start to here gives the wall-clock the run truly took; if the workers
        # couldn't keep up, this is much longer than ``runtime_per_function`` and the
        # achieved throughput falls below the target qps.
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
        """Choose the worker-pool size (== ``max_workers`` == pre-warm count) for ``test``.

        A short latency probe (the warmup) estimates per-call latency and Little's Law gives
        the concurrency the offered load needs (``qps x latency x headroom``), clamped to
        ``[_MIN_WORKER_THREADS, worker_thread_count]``. The whole returned pool is pre-warmed,
        so the cap keeps start-up memory/threads bounded; if the raw estimate exceeds the cap
        we say so, because that op may be client-thread-limited.
        """
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
            # The op wants more concurrency than we will pre-warm. Either it is too slow to
            # sustain this qps (the post-run warning will confirm), or the client is genuinely
            # thread-limited and needs a larger pool + more RAM.
            note = (
                f"; estimate {raw_estimate} exceeds the cap -- if the backend can sustain this, "
                f"raise worker_thread_count and provision more client RAM, else lower qps"
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

    def _probe_latency_ns(self, test: BenchmarkTest, probe_calls: int) -> int | None:
        """Run a brief, low-concurrency burst of ``test`` and return its p95 latency in ns.

        Probe calls are *not* recorded in the metrics; failures are ignored. Returns
        ``None`` if every probe call failed (so latency can't be estimated).
        """
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
        """Emit at most one congestion warning per test, targeted at the real cause.

        The primary axis is *throughput*: if the test couldn't drain calls as fast as they
        were offered (achieved qps well below target), its recorded latencies are dominated
        by backlog, not service time -- the numbers are invalid. Within that, ``peak_in_flight``
        localizes the bottleneck. The pre-warmed pool can never hold more in-flight calls than
        its size, so saturation (peak ~= pool) means workers were the limit and more help; an
        idle pool (peak << pool) during a shortfall means the limit is *downstream* of the pool
        -- the backend, the connection pool, or per-call client-side work -- which more workers
        cannot fix. A run that met its target throughput but still saw sustained lag is just
        transient jitter. Every branch requires sustained lag, never one blip.
        """
        if lag_count <= 0:
            return
        # Require sustained lag -- an absolute floor and a fraction of all calls -- so a
        # one-off blip (a single delayed pickup) can never trip a warning.
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
            # The pool sat mostly idle yet the run still fell behind: adding workers cannot
            # help because workers were never the scarce resource. The ceiling is downstream.
            warnings.warn(
                f"{backend}.{test.__name__}: could not sustain the target load ({rate}); "
                f"{lag_count}/{total_calls} calls backed up (worst dispatch lag "
                f"{worst_lag_ms:.1f} ms). The pre-warmed pool stayed nearly idle (peak "
                f"in-flight {peak_in_flight} of {pool_size}), so the bottleneck is downstream "
                f"of the worker pool -- the backend, the connection pool, or per-call "
                f"client-side serialization -- not thread count. Recorded latencies are "
                f"invalid (coordinated omission). Lower qps to a sustainable rate or reduce "
                f"per-call work; raising worker_thread_count will not help.",
                stacklevel=2,
            )
            return

        if saturated and at_cap:
            # Workers were the scarce resource and we are at the pool ceiling: this is a
            # client-side thread limit. More workers would help *if* the client can hold them.
            warnings.warn(
                f"{backend}.{test.__name__}: worker pool saturated at its ceiling "
                f"({pool_size}; {rate}); {lag_count}/{total_calls} calls queued (worst "
                f"dispatch lag {worst_lag_ms:.1f} ms). The client is thread-limited. If the "
                f"backend can sustain more, raise worker_thread_count and provision a larger "
                f"client (~8 MB RAM per thread); otherwise the backend can't keep up at this "
                f"rate -- lower qps.",
                stacklevel=2,
            )
            return

        if saturated:
            # Saturated below the cap: the probe under-estimated latency-under-load, so the
            # pool was sized too small even though the cap had headroom.
            warnings.warn(
                f"{backend}.{test.__name__}: the sized worker pool ({pool_size}) saturated "
                f"({rate}); {lag_count}/{total_calls} calls queued (worst dispatch lag "
                f"{worst_lag_ms:.1f} ms). Per-call latency under load ran higher than the "
                f"warmup probe predicted, so the pool was under-sized -- re-run (the warmup "
                f"will resize) or raise worker_thread_count. The recorded latencies for this "
                f"test include some queueing.",
                stacklevel=2,
            )
            return

        # Met the target rate, pool never saturated: a real but transient disturbance.
        warnings.warn(
            f"{backend}.{test.__name__}: sustained dispatch lag on {lag_count}/{total_calls} "
            f"calls (worst {worst_lag_ms:.1f} ms > {threshold_ms:.1f} ms) although the test "
            f"kept up overall ({rate}) and the pre-warmed worker pool never saturated (peak "
            f"in-flight {peak_in_flight} of {pool_size}). A worker was always available, so "
            f"this is not thread starvation and raising worker_thread_count will not help. "
            f"Likely causes are GC pauses, OS scheduler jitter, or backend latency spikes; "
            f"reduce per-call work or raise scheduler_thread_count for smoother pacing.",
            stacklevel=2,
        )


def _prewarm_pool(pool: ThreadPoolExecutor, count: int) -> None:
    """Force ``pool`` to create ``count`` worker threads before any real work is dispatched.

    Submits ``count`` tasks that all block on a shared barrier, so the pool must spin up
    ``count`` distinct threads to run them simultaneously; the barrier then releases them
    back to the pool as warm, idle workers. This moves OS thread-creation cost out of the
    timed run, where it would otherwise serialize inside ``ThreadPoolExecutor.submit`` (each
    growth blocks in ``Thread.start`` under the executor lock) and inflate dispatch lag.

    ``count`` must be ``<= pool``'s ``max_workers`` or the barrier can never release. If the
    OS refuses to create that many threads, ``submit`` raises (``RuntimeError``/``MemoryError``);
    we abort the barrier first so any threads already parked on it unblock and the pool can
    shut down cleanly, then re-raise for the caller to handle.
    """
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
