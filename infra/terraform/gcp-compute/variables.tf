variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "GCP region."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "GCP zone. Keeping nodes in one zone avoids cross-zone latency variance in early benchmarks."
  type        = string
  default     = "us-central1-a"
}

variable "name_prefix" {
  description = "Resource name prefix."
  type        = string

  validation {
    condition     = can(regex("^[a-z]([-a-z0-9]{0,30}[a-z0-9])?$", var.name_prefix))
    error_message = "name_prefix must be 1-32 characters, start with a lowercase letter, and contain only lowercase letters, numbers, and hyphens."
  }
}

variable "subnet_cidr" {
  description = "Private CIDR for benchmark nodes."
  type        = string
  default     = "10.100.0.0/24"
}

variable "enable_local_access" {
  description = "Whether to allow the local gcloud tunnel command through Google Cloud IAP."
  type        = bool
  default     = true
}

variable "enable_egress" {
  description = "Whether nodes have outbound internet via Cloud NAT. The workflow applies with this on to install software, then re-applies with it off to lock the nodes down. Nodes have no inbound internet access regardless."
  type        = bool
  default     = true
}

variable "labels" {
  description = "Additional GCP labels to apply to instances."
  type        = map(string)
  default     = {}
}

variable "enable_client" {
  description = "Whether to provision a private benchmark client VM in the same VPC and zone as the backends."
  type        = bool
  default     = false
}

variable "client_machine_type" {
  description = "Benchmark client machine type."
  type        = string
  default     = "c4-highcpu-48"
}

variable "client_boot_disk_size_gb" {
  description = "Benchmark client boot disk size in GB for uploaded projects and local virtual environments."
  type        = number
  default     = 100

  validation {
    condition     = var.client_boot_disk_size_gb >= 20
    error_message = "client_boot_disk_size_gb must be at least 20."
  }
}

variable "enable_redis" {
  description = "Whether to provision Redis."
  type        = bool
  default     = false
}

variable "redis_topology" {
  description = "Redis topology: standalone or sentinel."
  type        = string
  default     = "standalone"

  validation {
    condition     = contains(["standalone", "sentinel"], var.redis_topology)
    error_message = "redis_topology must be one of: standalone, sentinel."
  }
}

variable "enable_postgres" {
  description = "Whether to provision Postgres."
  type        = bool
  default     = false
}

variable "postgres_topology" {
  description = "Postgres topology: standalone or replicated."
  type        = string
  default     = "standalone"

  validation {
    condition     = contains(["standalone", "replicated"], var.postgres_topology)
    error_message = "postgres_topology must be one of: standalone, replicated."
  }
}

variable "postgres_max_connections" {
  description = "Postgres max_connections. Must exceed the benchmark worker pool size (worker_thread_count)."
  type        = number
  default     = 600

  validation {
    condition     = var.postgres_max_connections >= 100 && var.postgres_max_connections <= 10000
    error_message = "postgres_max_connections must be between 100 and 10000."
  }
}

variable "postgres_db_name" {
  description = "Benchmark database name."
  type        = string
  default     = "bench"
}

variable "postgres_db_user" {
  description = "Benchmark database user."
  type        = string
  default     = "bench"
}

variable "postgres_password" {
  description = "Benchmark Postgres password."
  type        = string
  default     = "benchpassword"
  sensitive   = true

  validation {
    condition     = length(var.postgres_password) >= 12
    error_message = "postgres_password must be at least 12 characters."
  }
}

variable "enable_aerospike" {
  description = "Whether to provision Aerospike Community Edition."
  type        = bool
  default     = true
}

variable "aerospike_node_count" {
  description = "Number of Aerospike nodes."
  type        = number
  default     = 1

  validation {
    condition     = var.aerospike_node_count >= 1
    error_message = "aerospike_node_count must be at least 1."
  }
}

variable "aerospike_replication_factor" {
  description = "Aerospike namespace replication factor. This is clamped to node_count by the module."
  type        = number
  default     = 1

  validation {
    condition     = var.aerospike_replication_factor >= 1
    error_message = "aerospike_replication_factor must be at least 1."
  }
}

variable "aerospike_namespace" {
  description = "Aerospike namespace name."
  type        = string
  default     = "test"
}

variable "aerospike_server_version" {
  description = "Aerospike Community Edition server version."
  type        = string
  default     = "8.0.0.15"
}
