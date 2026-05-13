variable "name_prefix" {
  description = "Resource name prefix."
  type        = string
}

variable "topology" {
  description = "standalone or replicated."
  type        = string

  validation {
    condition     = contains(["standalone", "replicated"], var.topology)
    error_message = "topology must be one of: standalone, replicated."
  }
}

variable "zone" {
  description = "GCP zone."
  type        = string
}

variable "machine_type" {
  description = "Instance type."
  type        = string
  default     = "n2d-standard-8"
}

variable "boot_image" {
  description = "Base image."
  type        = string
  default     = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts"
}

variable "subnet_self_link" {
  description = "VPC subnet self_link."
  type        = string
}

variable "subnet_cidr" {
  description = "VPC subnet CIDR for pg_hba.conf."
  type        = string
}

variable "local_ssd_count" {
  description = "Local NVMe SSDs per node."
  type        = number
  default     = 1
}

variable "postgres_major_version" {
  description = "Postgres major version from the PGDG apt repo."
  type        = number
  default     = 16
}

variable "db_name" {
  description = "Benchmark database name."
  type        = string
  default     = "bench"
}

variable "db_user" {
  description = "Benchmark role name."
  type        = string
  default     = "bench"
}

variable "db_password" {
  description = "Benchmark role password."
  type        = string
  sensitive   = true
}

variable "replication_user" {
  description = "Dedicated replication role."
  type        = string
  default     = "replicator"
}

variable "replication_password" {
  description = "Replication role password."
  type        = string
  sensitive   = true
}

variable "synchronous_commit" {
  description = "postgresql.conf synchronous_commit."
  type        = string
  default     = "on"

  validation {
    condition     = contains(["on", "off", "local", "remote_write", "remote_apply"], var.synchronous_commit)
    error_message = "synchronous_commit must be a valid postgresql.conf value."
  }
}

variable "labels" {
  description = "GCP labels applied to each instance."
  type        = map(string)
  default     = {}
}
