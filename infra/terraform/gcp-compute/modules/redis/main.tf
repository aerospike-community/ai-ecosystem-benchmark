locals {
  node_count = var.topology == "sentinel" ? 3 : 1

  node_names = [
    for i in range(local.node_count) : "${var.name_prefix}-redis-${i + 1}"
  ]

  node_roles = [
    for i in range(local.node_count) : (
      var.topology == "standalone" ? "standalone" : (i == 0 ? "primary" : "replica")
    )
  ]
}

resource "terraform_data" "init_inputs" {
  input = {
    local_ssd_count    = var.local_ssd_count
    master_name        = var.master_name
    redis_version      = var.redis_version
    sentinel_quorum    = var.sentinel_quorum
    startup_script_sha = filesha256("${path.module}/startup.sh")
    topology           = var.topology
  }
}

resource "google_compute_instance" "node" {
  count        = local.node_count
  name         = local.node_names[count.index]
  machine_type = var.machine_type
  zone         = var.zone
  tags         = ["bench-node", "redis"]
  labels       = merge(var.labels, { role = "redis" })

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
    name-prefix     = var.name_prefix
    node-index      = count.index
    node-role       = local.node_roles[count.index]
    topology        = var.topology
    redis-version   = var.redis_version
    sentinel-quorum = var.sentinel_quorum
    master-name     = var.master_name
  }

  metadata_startup_script = file("${path.module}/startup.sh")

  lifecycle {
    replace_triggered_by = [terraform_data.init_inputs]
  }
}
