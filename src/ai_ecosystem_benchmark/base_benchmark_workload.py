"""Base class for benchmark workloads."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from inspect import getmembers, ismethod
from typing import cast, final

BenchmarkTest = Callable[[], None]


class BaseBenchmarkWorkload(ABC):
    """Base class for benchmark workloads."""

    def __init__(
        self,
        aerospike_connection_string: str | None = None,
        postgres_connection_string: str | None = None,
        redis_connection_string: str | None = None,
    ) -> None:
        """Initialize the workload."""
        self.aerospike_connection_string = aerospike_connection_string
        self.postgres_connection_string = postgres_connection_string
        self.redis_connection_string = redis_connection_string

    @final
    def is_aerospike_enabled(self) -> bool:
        """Return whether Aerospike should be benchmarked."""
        return self.aerospike_connection_string is not None

    @final
    def is_postgres_enabled(self) -> bool:
        """Return whether Postgres should be benchmarked."""
        return self.postgres_connection_string is not None

    @final
    def is_redis_enabled(self) -> bool:
        """Return whether Redis should be benchmarked."""
        return self.redis_connection_string is not None

    @final
    def get_aerospike_tests(self) -> list[BenchmarkTest]:
        """Return Aerospike benchmark methods."""
        if not self.is_aerospike_enabled():
            return []
        return self._get_tests_with_prefix("aerospike")

    @final
    def get_postgres_tests(self) -> list[BenchmarkTest]:
        """Return Postgres benchmark methods."""
        if not self.is_postgres_enabled():
            return []
        return self._get_tests_with_prefix("postgres")

    @final
    def get_redis_tests(self) -> list[BenchmarkTest]:
        """Return Redis benchmark methods."""
        if not self.is_redis_enabled():
            return []
        return self._get_tests_with_prefix("redis")

    @abstractmethod
    def setup(self) -> None:
        """Prepare the workload before benchmark execution."""
        ...

    @abstractmethod
    def between_benchmarks(self) -> None:
        """Run between benchmark executions."""
        ...

    @abstractmethod
    def teardown(self) -> None:
        """Clean up the workload after benchmark execution."""
        ...

    def _get_tests_with_prefix(self, prefix: str) -> list[BenchmarkTest]:
        return [
            cast(BenchmarkTest, method)
            for name, method in getmembers(self, predicate=ismethod)
            if name.startswith(prefix)
        ]
