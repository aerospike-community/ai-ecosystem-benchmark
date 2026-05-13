variable "name_prefix" {
  description = "Resource name prefix."
  type        = string
}

variable "node_count" {
  description = "Number of Aerospike nodes."
  type        = number
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

variable "local_ssd_count" {
  description = "Local NVMe SSDs per node."
  type        = number
  default     = 1
}

variable "device_partitions_per_ssd" {
  description = "Partitions per NVMe device."
  type        = number
  default     = 4
}

variable "replication_factor" {
  description = "Replication factor for the bench namespace."
  type        = number
  default     = 1
}

variable "namespace" {
  description = "Aerospike namespace name."
  type        = string
  default     = "bench"
}

variable "server_version" {
  description = "Aerospike Enterprise server version."
  type        = string
  default     = "8.0.0.15"
}

variable "tools_version" {
  description = "Aerospike tools version bundled with server_version."
  type        = string
  default     = "13.0.0"
}

variable "features_conf_path" {
  description = "Path to a valid Aerospike Enterprise feature-key file."
  type        = string
}

variable "commit_to_device" {
  description = "Aerospike commit-to-device flag."
  type        = bool
  default     = false
}

variable "labels" {
  description = "GCP labels applied to each instance."
  type        = map(string)
  default     = {}
}
