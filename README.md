# AI Ecosystem Benchmark

Generalized framework for benchmarking AI tools that depend on database backends.

The first infrastructure target is a GCP Compute Terraform stack that can provision any combination of Redis, Postgres, and Aerospike benchmark clusters. The GitHub Actions workflow in `.github/workflows/terraform.yml` runs Terraform manually through `workflow_dispatch` and prints non-sensitive endpoint details after apply.

See `infra/terraform/gcp-compute/README.md` for setup details.
