output "subnet_self_link" {
  description = "Subnetwork self_link."
  value       = google_compute_subnetwork.this.self_link
}

output "client_subnet_self_link" {
  description = "Client subnetwork self_link, or null when the client subnet is disabled."
  value       = var.enable_client_egress ? google_compute_subnetwork.client[0].self_link : null
}

output "subnet_cidr" {
  description = "Subnet CIDR."
  value       = google_compute_subnetwork.this.ip_cidr_range
}

output "client_subnet_cidr" {
  description = "Client subnet CIDR, or null when the client subnet is disabled."
  value       = var.enable_client_egress ? google_compute_subnetwork.client[0].ip_cidr_range : null
}

output "network_name" {
  description = "Network name."
  value       = google_compute_network.this.name
}
