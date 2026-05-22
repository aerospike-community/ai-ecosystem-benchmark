import pytest

from ai_ecosystem_benchmark import BaseBenchmarkWorkload


class ExampleWorkload(BaseBenchmarkWorkload):
    def __init__(
        self,
        aerospike_connection_string: str | None = None,
        postgres_connection_string: str | None = None,
        redis_connection_string: str | None = None,
    ) -> None:
        super().__init__(
            aerospike_connection_string=aerospike_connection_string,
            postgres_connection_string=postgres_connection_string,
            redis_connection_string=redis_connection_string,
        )
        self.foo = "foo"

    def setup(self) -> None:
        return None

    def between_benchmarks(self) -> None:
        return None

    def teardown(self) -> None:
        return None

    def aerospike_test_insert(self) -> None:
        return None

    def aerospike_test_query(self) -> None:
        self.foo = "bar"

    def postgres_test_insert(self) -> None:
        return None

    def redis_test_insert(self) -> None:
        return None

    def helper(self) -> None:
        return None


class MissingLifecycleWorkload(BaseBenchmarkWorkload):
    pass


def test_backend_enabled_checks_reflect_connection_strings() -> None:
    workload = ExampleWorkload(
        aerospike_connection_string="aerospike://localhost:3000",
        redis_connection_string="redis://localhost:6379/0",
    )

    assert workload.is_aerospike_enabled()
    assert not workload.is_postgres_enabled()
    assert workload.is_redis_enabled()


def test_backend_tests_are_empty_when_methods_exist_but_backend_is_disabled() -> None:
    workload = ExampleWorkload()

    assert workload.get_aerospike_tests() == []
    assert workload.get_postgres_tests() == []
    assert workload.get_redis_tests() == []


def test_backend_tests_are_discovered_by_prefix() -> None:
    workload = ExampleWorkload(
        aerospike_connection_string="aerospike://localhost:3000",
        postgres_connection_string="postgresql://bench:password@localhost:5432/bench",
        redis_connection_string="redis://localhost:6379/0",
    )

    assert [test.__name__ for test in workload.get_aerospike_tests()] == [
        "aerospike_test_insert",
        "aerospike_test_query",
    ]
    assert [test.__name__ for test in workload.get_postgres_tests()] == ["postgres_test_insert"]
    assert [test.__name__ for test in workload.get_redis_tests()] == ["redis_test_insert"]


def test_discovered_test_function_runs_against_workload_instance() -> None:
    workload = ExampleWorkload(aerospike_connection_string="aerospike://localhost:3000")
    aerospike_tests = workload.get_aerospike_tests()
    aerospike_test_query = next(
        test for test in aerospike_tests if test.__name__ == "aerospike_test_query"
    )

    aerospike_test_query()

    assert workload.foo == "bar"


def test_abstract_lifecycle_methods_are_required() -> None:
    with pytest.raises(TypeError):
        MissingLifecycleWorkload()  # type: ignore[abstract]
