output "subnet_self_link" {
  description = "Subnetwork self_link."
  value       = google_compute_subnetwork.this.self_link
}

output "subnet_cidr" {
  description = "Subnet CIDR."
  value       = google_compute_subnetwork.this.ip_cidr_range
}

output "network_name" {
  description = "Network name."
  value       = google_compute_network.this.name
}
