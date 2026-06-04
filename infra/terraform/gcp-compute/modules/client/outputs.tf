output "name" {
  description = "Benchmark client instance name."
  value       = google_compute_instance.this.name
}

output "internal_ip" {
  description = "Benchmark client internal IP."
  value       = google_compute_instance.this.network_interface[0].network_ip
}

output "external_ip" {
  description = "Benchmark client external IP for outbound internet access."
  value       = google_compute_instance.this.network_interface[0].access_config[0].nat_ip
}

output "workdir" {
  description = "Directory intended for uploaded benchmark projects."
  value       = "/srv/benchmarks"
}
