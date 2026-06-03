# GCP Compute Terraform Stack

This stack provisions benchmark database backends on GCP Compute Engine:

- Redis Stack: standalone or three-node Sentinel topology, with RediSearch enabled.
- Postgres: standalone or three-node primary plus async replicas.
- Aerospike Community Edition: one or more nodes with mesh heartbeat.
- Optional benchmark client VM in the same VPC and zone as the backends.

The GitHub Action at `.github/workflows/terraform.yml` runs this stack manually.
It is currently pinned to the `firefly-aerospike` GCP project; use `name_prefix` in the manual trigger to separate test deployments.

## Important Assumptions

- This is a GCP starter because the benchmark examples in this repo are already GCP Compute based.
- Endpoints printed by Terraform are private/internal IPs. Use them from benchmark clients running in the same GCP VPC, including the optional Terraform-managed client VM.
- Do not use the private/internal IPs directly from your computer. For local access, run the `gcloud` command printed by the workflow and then connect to `127.0.0.1`.
- Nodes have no external IP and, after provisioning, no internet access at all. During `apply`, the workflow temporarily enables Cloud NAT (egress only) so the nodes can install software, waits for each node to finish, then removes Cloud NAT so the running nodes have no outbound internet. Local access uses Google Cloud IAP, which reaches the node over its internal IP, so no public IP or CIDR is needed and nothing is exposed to the inbound internet.
- Aerospike uses Community Edition packages, so no feature key is required for the current starter stack.
- Redis uses Redis Stack rather than vanilla Redis OSS so benchmark workloads that depend on Redis modules work out of the box. RediSearch and RedisJSON are required; RedisBloom and RedisTimeSeries are loaded when the package provides them.
- Terraform state uses a GCS backend. Create the state bucket before using the GitHub Action.

## Manual Workflow Inputs

- `terraform_action`: choose `plan` to preview, `apply` to create/update resources, or `destroy` to delete them.
- `name_prefix`: short label added to all resources, such as `test0`.
- `enable_aerospike`, `enable_redis`, `enable_postgres`: choose which databases to create.
- `aerospike_namespace`: namespace to create when Aerospike is enabled. The default is `test`.
- `postgres_password`: password for the Postgres `bench` user when Postgres is enabled. The default is `benchpassword`.
- `enable_local_access`: keep this on if you want the workflow summary to include a copy-paste command for connecting from your computer. Turn it off only when benchmark clients run in GCP and nobody needs local access.
- `enable_client`: create a private benchmark client VM in the same VPC and zone as the backends. The client is provisioned with Python 3, `uv`, `uvx`, Git, rsync, and build essentials.

Local access is still private: the databases listen on private/internal IPs, and the `gcloud` command forwards local ports through Google Cloud IAP. You do not need to open database ports to the public internet.

## Which Endpoint To Use

- From the Terraform-managed benchmark client VM, or any benchmark client running in the benchmark VPC, use the private/internal host printed by the workflow.
- From your computer, run the `gcloud` command from the workflow summary and then connect to `127.0.0.1` on the printed port.
- Nodes have no external IP and no internet access once provisioning finishes (Cloud NAT exists only during install and is removed automatically). Inbound access is limited to SSH over Google Cloud IAP, gated by GCP IAM.

If `enable_client` is true, the workflow summary also prints commands to SSH to the client and upload a local project directory to `/srv/benchmarks` with `gcloud compute scp --recurse ... --tunnel-through-iap`. Keep `enable_local_access` true when you want to SSH or upload from your computer. Once connected, use the private/internal backend hosts printed in the same summary.

Postgres always requires a password. Use the `postgres_password` workflow input when connecting. The default is `benchpassword`, so there is no separate Terraform command to fetch it. Changing `postgres_password`, `postgres_db_name`, or `postgres_db_user` after Postgres has already been created will recreate the Postgres nodes so the new login settings take effect.

## GitHub Secrets

Configure these repository secrets:

- `GCP_WORKLOAD_IDENTITY_PROVIDER`: Workload Identity Provider resource name for GitHub OIDC.
- `GCP_SERVICE_ACCOUNT`: Service account email used by Terraform.
- `TF_STATE_BUCKET`: GCS bucket for Terraform state.

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
enable_aerospike = true
enable_redis     = false
enable_postgres  = false
enable_client    = true
```

Use `redis_topology = "sentinel"` for a three-node Redis deployment and `postgres_topology = "replicated"` for a three-node Postgres deployment.

Set `aerospike_namespace` to choose the Aerospike namespace name. If unset, Terraform creates the default `test` namespace.

## Current Direction

You are not off track. The one architectural choice to make explicit early is where benchmarks run. Provisioning infrastructure from GitHub Actions is fine, but the benchmark clients should be close to the databases, usually inside the same VPC, so private IPs and realistic latency are preserved.
