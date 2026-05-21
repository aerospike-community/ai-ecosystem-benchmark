# Contributing to AI Ecosystem Benchmark

Thank you for your interest in contributing to this Aerospike project.

## Development Setup

This repo uses [`uv`](https://docs.astral.sh/uv/) for Python dependency
management, [`ruff`](https://docs.astral.sh/ruff/) for linting/formatting,
[`mypy`](https://mypy.readthedocs.io/) for type checking, and
[`pre-commit`](https://pre-commit.com/) for local commit hooks.

Sync the development environment:

```bash
uv sync
```

Install the git hooks once:

```bash
uv run pre-commit install
```

Run the same checks manually:

```bash
uv run pre-commit run --all-files
```

Run tests directly:

```bash
uv run pytest
```
