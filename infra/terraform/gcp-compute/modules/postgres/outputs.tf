output "nodes" {
  description = "Per-node info. Node 0 is the primary."
  value = [
    for inst in google_compute_instance.node : {
      name        = inst.name
      internal_ip = inst.network_interface[0].network_ip
      external_ip = inst.network_interface[0].access_config[0].nat_ip
    }
  ]
}

output "primary_internal_ip" {
  description = "Internal IP of the writable primary."
  value       = google_compute_instance.node[0].network_interface[0].network_ip
}

output "replica_internal_ips" {
  description = "Internal IPs of async replicas."
  value = [
    for i, inst in google_compute_instance.node : inst.network_interface[0].network_ip
    if i > 0
  ]
}

output "topology" {
  description = "Postgres topology."
  value       = var.topology
}
