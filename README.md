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

## Infrastructure

The first infrastructure target is a GCP Compute Terraform stack that can provision any combination of Redis, Postgres, and Aerospike benchmark clusters. The GitHub Actions workflow in `.github/workflows/terraform.yml` runs Terraform manually through `workflow_dispatch` and prints non-sensitive endpoint details after apply.

See `infra/terraform/gcp-compute/README.md` for setup details.
