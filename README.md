# AI Ecosystem Benchmark

Generalized framework for benchmarking AI tools that depend on database backends.

## Python Package

This repository is also a Python package named `ai-ecosystem-benchmark`. The
package is intentionally minimal for now while the benchmark framework takes
shape.

Install and sync the development environment with [`uv`](https://docs.astral.sh/uv/):

```bash
uv sync
```

Run the local checks:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run deptry .
```

Install pre-commit hooks once per checkout:

```bash
uv run pre-commit install
```

## Implementing a Workload

Benchmark workloads should subclass `BaseBenchmarkWorkload` and implement the
required lifecycle hooks:

- `setup()`: prepare clients, data, or other state before benchmarks run.
- `between_benchmarks()`: reset or pause between benchmark executions.
- `teardown()`: clean up resources after benchmarks finish.

Connection strings are optional. If a connection string is omitted, that backend
is assumed to be disabled for the benchmark run.

Backend-specific benchmark methods are discovered by name:

- Aerospike methods start with `aerospike`
- Postgres methods start with `postgres`
- Redis methods start with `redis`

```python
from ai_ecosystem_benchmark import BaseBenchmarkWorkload


class MyWorkload(BaseBenchmarkWorkload):
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

    def setup(self) -> None:
        return None

    def between_benchmarks(self) -> None:
        return None

    def teardown(self) -> None:
        return None

    def aerospike_test_insert(self) -> None:
        ...

    def redis_test_lookup(self) -> None:
        ...
```

## Infrastructure

The first infrastructure target is a GCP Compute Terraform stack that can provision any combination of Redis, Postgres, and Aerospike benchmark clusters. The GitHub Actions workflow in `.github/workflows/terraform.yml` runs Terraform manually through `workflow_dispatch` and prints non-sensitive endpoint details after apply.

See `infra/terraform/gcp-compute/README.md` for setup details.
