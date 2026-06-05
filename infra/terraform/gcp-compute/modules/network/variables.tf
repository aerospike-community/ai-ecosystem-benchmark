variable "name_prefix" {
  description = "Resource name prefix."
  type        = string
}

variable "enable_local_access" {
  description = "Whether to allow SSH through Google Cloud IAP for local tunnel access."
  type        = bool
  default     = true
}

variable "enable_egress" {
  description = "Whether to provision Cloud NAT so nodes have outbound internet (egress only)."
  type        = bool
  default     = true
}

variable "subnet_cidr" {
  description = "Private CIDR for the benchmark subnet."
  type        = string
  default     = "10.100.0.0/24"
}

variable "enable_client_egress" {
  description = "Whether to provision a dedicated client subnet with persistent outbound internet through Cloud NAT."
  type        = bool
  default     = false
}

variable "client_subnet_cidr" {
  description = "Private CIDR for the benchmark client subnet."
  type        = string
  default     = "10.101.0.0/24"
}
