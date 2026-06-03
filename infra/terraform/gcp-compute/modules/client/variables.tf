variable "name_prefix" {
  description = "Resource name prefix."
  type        = string
}

variable "zone" {
  description = "GCP zone for the benchmark client."
  type        = string
}

variable "subnet_self_link" {
  description = "Subnetwork self_link."
  type        = string
}

variable "machine_type" {
  description = "Benchmark client machine type."
  type        = string
  default     = "c3-standard-4"
}

variable "boot_image" {
  description = "Boot image for the benchmark client."
  type        = string
  default     = "projects/debian-cloud/global/images/family/debian-12"
}

variable "boot_disk_size_gb" {
  description = "Boot disk size in GB for uploaded benchmark projects and local virtual environments."
  type        = number
  default     = 100

  validation {
    condition     = var.boot_disk_size_gb >= 20
    error_message = "boot_disk_size_gb must be at least 20."
  }
}

variable "labels" {
  description = "Labels to apply to the client instance."
  type        = map(string)
  default     = {}
}
