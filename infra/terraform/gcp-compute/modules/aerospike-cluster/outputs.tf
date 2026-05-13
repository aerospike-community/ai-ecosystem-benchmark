output "nodes" {
  description = "Per-node info. Index 0 is a reasonable client seed."
  value = [
    for inst in google_compute_instance.node : {
      name        = inst.name
      internal_ip = inst.network_interface[0].network_ip
      external_ip = inst.network_interface[0].access_config[0].nat_ip
    }
  ]
}

output "seed_internal_ip" {
  description = "Internal IP of node 0, suitable for AEROSPIKE_HOST."
  value       = google_compute_instance.node[0].network_interface[0].network_ip
}
