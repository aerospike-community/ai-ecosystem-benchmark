variable "name_prefix" {
  description = "Resource name prefix."
  type        = string
}

variable "enable_local_access" {
  description = "Whether to allow SSH through Google Cloud IAP for local tunnel access."
  type        = bool
  default     = true
}

variable "subnet_cidr" {
  description = "Private CIDR for the benchmark subnet."
  type        = string
  default     = "10.100.0.0/24"
}
