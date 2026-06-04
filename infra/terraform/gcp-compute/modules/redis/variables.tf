variable "name_prefix" {
  description = "Resource name prefix."
  type        = string
}

variable "topology" {
  description = "standalone or sentinel."
  type        = string

  validation {
    condition     = contains(["standalone", "sentinel"], var.topology)
    error_message = "topology must be one of: standalone, sentinel."
  }
}

variable "zone" {
  description = "GCP zone."
  type        = string
}

variable "machine_type" {
  description = "Instance type."
  type        = string
  default     = "n2d-standard-16"
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

variable "local_ssd_count" {
  description = "Local NVMe SSDs per node for Redis AOF persistence."
  type        = number
  default     = 2
}

variable "redis_version" {
  description = "Redis major version."
  type        = string
  default     = "7"
}

variable "sentinel_quorum" {
  description = "Sentinel failover quorum."
  type        = number
  default     = 2
}

variable "master_name" {
  description = "Sentinel master name."
  type        = string
  default     = "bench-master"
}

variable "labels" {
  description = "GCP labels applied to each instance."
  type        = map(string)
  default     = {}
}
