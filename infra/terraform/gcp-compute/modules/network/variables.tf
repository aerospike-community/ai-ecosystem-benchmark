variable "name_prefix" {
  description = "Resource name prefix."
  type        = string
}

variable "allowed_ssh_cidr" {
  description = "CIDR allowed to SSH in. Empty means no SSH ingress rule."
  type        = string
  default     = ""
}

variable "subnet_cidr" {
  description = "Private CIDR for the benchmark subnet."
  type        = string
  default     = "10.100.0.0/24"
}
