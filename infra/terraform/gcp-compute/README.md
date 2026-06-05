# GCP Compute Terraform Stack

This stack brings up benchmark infrastructure on GCP Compute Engine.

## What It Creates

- A VPC with private subnets.
- Optional Aerospike Community Edition nodes.
- Optional Redis Stack nodes.
- Optional Postgres nodes.
- Optional benchmark client VM with Python 3, `uv`, `uvx`, Git, rsync, build tools, and `libpq5`.
- IAP SSH firewall access when `enable_local_access = true`.

Database nodes use private IPs only. The benchmark client also has no external IP, but keeps outbound internet access through Cloud NAT when enabled.

## Run From GitHub Actions

Use `.github/workflows/terraform.yml`.

Main inputs:

- `terraform_action`: `plan`, `apply`, or `destroy`.
- `name_prefix`: prefix for resource names.
- `enable_aerospike`, `enable_redis`, `enable_postgres`: choose which databases to create.
- `enable_client`: create the benchmark client VM.
- `enable_local_access`: print IAP SSH/SCP commands in the workflow summary.
- `aerospike_namespace`: Aerospike namespace, default `test`.
- `redis_topology`: `standalone` or `sentinel`.
- `postgres_topology`: `standalone` or `replicated`.
- `postgres_password`: password for the Postgres `bench` user.

After `apply`, the workflow summary prints endpoint details and client access commands.

## Connect

From the Terraform-managed client VM, use the private/internal hosts printed by the workflow.

From your computer, use the `gcloud` tunnel command printed by the workflow, then connect to `127.0.0.1`.

If `enable_client = true`, the workflow summary also prints commands to SSH to the client and upload a project directory to `/srv/benchmarks`.

## Local Usage

Copy `terraform.tfvars.example` to a local `.tfvars` file and adjust the enabled services.

```bash
cd infra/terraform/gcp-compute
terraform init \
  -backend-config="bucket=<state-bucket>" \
  -backend-config="prefix=ai-ecosystem-benchmark/gcp-compute/local"
terraform plan -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
terraform output endpoints
```

Example service selection:

```hcl
enable_aerospike = true
enable_redis     = false
enable_postgres  = false
enable_client    = true
```

## GitHub Secrets

Configure these repository secrets before running the GitHub Action:

- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT`
- `TF_STATE_BUCKET`
