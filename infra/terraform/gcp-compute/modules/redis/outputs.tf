output "nodes" {
  description = "Per-node info. Node 0 is the primary in sentinel topology."
  value = [
    for inst in google_compute_instance.node : {
      name        = inst.name
      internal_ip = inst.network_interface[0].network_ip
      external_ip = inst.network_interface[0].access_config[0].nat_ip
    }
  ]
}

output "primary_internal_ip" {
  description = "Internal IP of the primary node."
  value       = google_compute_instance.node[0].network_interface[0].network_ip
}

output "sentinel_hosts" {
  description = "Internal IPs of sentinel-running nodes."
  value = var.topology == "sentinel" ? [
    for inst in google_compute_instance.node : inst.network_interface[0].network_ip
  ] : []
}

output "master_name" {
  description = "Sentinel master name."
  value       = var.master_name
}

output "topology" {
  description = "Redis topology."
  value       = var.topology
}
