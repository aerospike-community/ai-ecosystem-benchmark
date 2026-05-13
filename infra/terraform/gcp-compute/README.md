# GCP Compute Terraform Stack

This stack provisions benchmark database backends on GCP Compute Engine:

- Redis: standalone or three-node Sentinel topology.
- Postgres: standalone or three-node primary plus async replicas.
- Aerospike Enterprise: one or more nodes with mesh heartbeat.

The GitHub Action at `.github/workflows/terraform.yml` runs this stack manually with `workflow_dispatch`.

## Important Assumptions

- This is a GCP starter because the benchmark examples in this repo are already GCP Compute based.
- Endpoints printed by Terraform are private VPC IPs. GitHub-hosted runners can create the resources, but your actual benchmark runner needs to run inside the VPC, over VPN, through a bastion, or through another private connectivity path.
- Database ports are not opened to the public internet. Optional SSH ingress can be enabled with `allowed_ssh_cidr`, but the default creates no SSH firewall rule.
- Aerospike uses Enterprise packages and requires a valid `features.conf` file.
- Terraform state uses a GCS backend. Create the state bucket before using the GitHub Action.

## GitHub Secrets

Configure these repository secrets:

- `GCP_WORKLOAD_IDENTITY_PROVIDER`: Workload Identity Provider resource name for GitHub OIDC.
- `GCP_SERVICE_ACCOUNT`: Service account email used by Terraform.
- `TF_STATE_BUCKET`: GCS bucket for Terraform state.
- `AEROSPIKE_FEATURES_CONF`: Contents of Aerospike `features.conf`. Required only when `enable_aerospike` is true.

The service account needs permissions to read/write the state bucket and manage Compute Engine resources. For a first pass in a sandbox project, `roles/compute.admin`, `roles/iam.serviceAccountUser`, and storage access to the state bucket are usually enough.

## Local Usage

```bash
cd infra/terraform/gcp-compute
terraform init \
  -backend-config="bucket=<state-bucket>" \
  -backend-config="prefix=ai-ecosystem-benchmark/gcp-compute/local"
terraform plan -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
terraform output endpoints
```

Copy `terraform.tfvars.example` to a local `.tfvars` file and adjust the enabled backends.

## Backend Selection

Each backend can be toggled independently:

```hcl
enable_redis     = true
enable_postgres  = true
enable_aerospike = false
```

Use `redis_topology = "sentinel"` for a three-node Redis deployment and `postgres_topology = "replicated"` for a three-node Postgres deployment.

## Current Direction

You are not off track. The one architectural choice to make explicit early is where benchmarks run. Provisioning infrastructure from GitHub Actions is fine, but the benchmark clients should be close to the databases, usually inside the same VPC, so private IPs and realistic latency are preserved.
