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
  default     = "ai-bench"

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

variable "allowed_ssh_cidr" {
  description = "Optional CIDR allowed to SSH to nodes. Leave empty to create no SSH ingress rule."
  type        = string
  default     = ""
}

variable "labels" {
  description = "Additional GCP labels to apply to instances."
  type        = map(string)
  default     = {}
}

variable "enable_redis" {
  description = "Whether to provision Redis."
  type        = bool
  default     = true
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

variable "enable_aerospike" {
  description = "Whether to provision Aerospike Enterprise."
  type        = bool
  default     = false
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

variable "aerospike_features_conf_path" {
  description = "Path to an Aerospike Enterprise features.conf file. Required when enable_aerospike is true."
  type        = string
  default     = null
}

variable "aerospike_server_version" {
  description = "Aerospike Enterprise server version."
  type        = string
  default     = "8.0.0.15"
}
