output "name" {
  description = "Benchmark client instance name."
  value       = google_compute_instance.this.name
}

output "internal_ip" {
  description = "Benchmark client internal IP."
  value       = google_compute_instance.this.network_interface[0].network_ip
}

output "workdir" {
  description = "Directory intended for uploaded benchmark projects."
  value       = "/srv/benchmarks"
}
