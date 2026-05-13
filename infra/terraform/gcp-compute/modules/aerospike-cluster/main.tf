locals {
  effective_replication_factor = min(var.replication_factor, var.node_count)

  node_names = [
    for i in range(var.node_count) : "${var.name_prefix}-aerospike-${i + 1}"
  ]
}

resource "google_compute_instance" "node" {
  count        = var.node_count
  name         = local.node_names[count.index]
  machine_type = var.machine_type
  zone         = var.zone
  tags         = ["bench-node", "aerospike"]
  labels       = merge(var.labels, { role = "aerospike" })

  boot_disk {
    initialize_params {
      image = var.boot_image
      size  = 50
      type  = "pd-balanced"
    }
  }

  dynamic "scratch_disk" {
    for_each = range(var.local_ssd_count)
    content {
      interface = "NVME"
    }
  }

  network_interface {
    subnetwork = var.subnet_self_link
    access_config {}
  }

  metadata = {
    name-prefix               = var.name_prefix
    node-index                = count.index
    cluster-size              = var.node_count
    aerospike-version         = var.server_version
    aerospike-tools-version   = var.tools_version
    aerospike-namespace       = var.namespace
    replication-factor        = local.effective_replication_factor
    local-ssd-count           = var.local_ssd_count
    device-partitions-per-ssd = var.device_partitions_per_ssd
    commit-to-device          = var.commit_to_device
  }

  metadata_startup_script = file("${path.module}/startup.sh")
}
